from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.backend import moex, repositories
from app.backend.models import Bond, Issuer, MarketData, RateCurve
from app.backend.moex import MoexClient
from cdslib import Tenor, TenorUnit, bootstrap_credit_curve

_MAX_GRID_MONTHS = 720


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


# --- serializers ----------------------------------------------------------


def issuer_dict(issuer: Issuer) -> dict[str, Any]:
    return {
        "id": issuer.id,
        "name": issuer.name,
        "recovery_rate": issuer.recovery_rate,
        "moex_emitent_id": issuer.moex_emitent_id,
        "bond_count": len(issuer.bonds),
    }


def bond_dict(bond: Bond) -> dict[str, Any]:
    return {
        "isin": bond.isin,
        "secid": bond.secid,
        "name": bond.name,
        "shortname": bond.shortname,
        "currency": bond.currency,
        "face_value": bond.face_value,
        "maturity_date": _iso(bond.maturity_date),
        "offer_date": _iso(bond.offer_date),
        "coupon_percent": bond.coupon_percent,
        "list_level": bond.list_level,
        "board": bond.board,
    }


def market_dict(md: MarketData) -> dict[str, Any]:
    return {
        "isin": md.bond_isin,
        "trade_date": md.trade_date.isoformat(),
        "source": md.source,
        "is_priced": md.is_priced,
        "clean_price": md.clean_price,
        "dirty_price": md.dirty_price,
        "accrued": md.accrued,
        "weight": md.weight,
        "bid": md.bid,
        "ask": md.ask,
        "last": md.last,
        "waprice": md.waprice,
    }


def rate_curve_dict(record: RateCurve) -> dict[str, Any]:
    return {
        "id": record.id,
        "rate_index": record.rate_index,
        "trade_date": record.trade_date.isoformat(),
        "spot_date": _iso(record.spot_date),
        "average_adjustment_bps": record.average_adjustment_bps,
        "max_adjustment_bps": record.max_adjustment_bps,
        "nodes": len(json.loads(record.points_json)),
    }


# --- operations -----------------------------------------------------------


def add_bond(
    session: Session, client: MoexClient, issuer_id: int, isin: str
) -> Bond:
    ref = client.security(isin)
    schedule = client.bondization(isin)
    return repositories.add_bond(session, issuer_id, ref, schedule, board=moex.DEFAULT_BOARD)


def fetch_market(
    session: Session, client: MoexClient, isin: str, trade_date: date
) -> MarketData:
    cached = repositories.get_market_data(session, isin, trade_date)
    if cached is not None and cached.is_priced:
        return cached
    quote = client.quote(isin, trade_date)
    return repositories.upsert_market_data(session, isin, quote)


def credit_curve(
    session: Session,
    client: MoexClient,
    issuer_id: int,
    rate_curve_id: int,
    trade_date: date | None,
) -> dict[str, Any]:
    issuer = repositories.get_issuer(session, issuer_id)
    if issuer is None:
        raise KeyError(f"Unknown issuer {issuer_id}")
    rate_record = repositories.get_rate_curve(session, rate_curve_id)
    if rate_record is None:
        raise KeyError(f"Unknown rate curve {rate_curve_id}")

    discount = repositories.to_zero_curve(rate_record)
    base = discount.base_date
    trade = trade_date or rate_record.trade_date

    quant_bonds = []
    dirty_prices: list[float] = []
    weights: list[float] = []
    meta: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for bond in repositories.list_bonds(session, issuer_id):
        try:
            quant = repositories.to_cdslib_bond(bond)
        except ValueError as exc:
            skipped.append({"isin": bond.isin, "reason": str(exc)})
            continue
        if quant.effective_maturity <= base:
            skipped.append({"isin": bond.isin, "reason": "matured before trade date"})
            continue
        try:
            md = fetch_market(session, client, bond.isin, trade)
        except Exception:  # noqa: BLE001 - network/parse failure -> skip this bond
            skipped.append({"isin": bond.isin, "reason": "market data unavailable"})
            continue
        if not md.is_priced or md.dirty_price is None:
            skipped.append({"isin": bond.isin, "reason": "no usable price"})
            continue
        quant_bonds.append(quant)
        dirty_prices.append(md.dirty_price)
        weights.append(md.weight or 1.0)
        meta.append(
            {
                "isin": bond.isin,
                "shortname": bond.shortname,
                "source": md.source,
                "clean_price": md.clean_price,
            }
        )

    if not quant_bonds:
        raise ValueError("No priced bonds available for this issuer / trade date")

    result = bootstrap_credit_curve(
        quant_bonds, dirty_prices, discount, issuer.recovery_rate, weights
    )
    recovery = issuer.recovery_rate

    fits = []
    for info, fit in zip(meta, result.fits, strict=True):
        fits.append(
            {
                **info,
                "maturity_date": fit.effective_maturity.isoformat(),
                "years": fit.years,
                "market_dirty": round(fit.market_dirty, 4),
                "model_dirty": round(fit.model_dirty, 4),
                "residual": round(fit.residual, 4),
                "weight": round(fit.weight, 4),
            }
        )
    fits.sort(key=lambda r: r["years"])

    curve = result.curve
    last_days = max(p.tenor_days for p in curve.points())
    nodes: list[dict[str, Any]] = []
    export: dict[str, float] = {}
    prev_h = 0.0
    prev_t = 0.0
    for month in range(1, _MAX_GRID_MONTHS + 1):
        node_date = Tenor(month, TenorUnit.MONTH).add_to(base)
        days = (node_date - base).days
        if days <= 0:
            continue
        hazard = curve.hazard_days(days)
        survival = curve.survival_days(days)
        years = days / 365.0
        integrated = -math.log(survival) if survival > 0 else prev_h
        forward = (integrated - prev_h) / (years - prev_t) if years > prev_t else hazard
        prev_h, prev_t = integrated, years
        nodes.append(
            {
                "date": node_date.isoformat(),
                "years": round(years, 4),
                "hazard": round(hazard * 100.0, 6),
                "forward": round(forward * 100.0, 6),
                "spread": round(hazard * (1.0 - recovery) * 100.0, 6),
                "survival": round(survival, 8),
            }
        )
        export[node_date.isoformat()] = round(hazard, 8)
        if days >= last_days:
            break

    return {
        "issuer_id": issuer_id,
        "issuer_name": issuer.name,
        "rate_curve_id": rate_curve_id,
        "base_date": base.isoformat(),
        "trade_date": trade.isoformat(),
        "recovery_rate": recovery,
        "smoothing_lambda": result.smoothing_lambda,
        "curve": nodes,
        "fits": fits,
        "skipped": skipped,
        "hazard_export": export,
    }
