from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.backend import moex, repositories
from app.backend.models import Bond, Issuer, MarketData, RateCurve
from app.backend.moex import MoexClient
from cdslib import Bond as QuantBond
from cdslib import (
    CreditCurveResult,
    Tenor,
    TenorUnit,
    ZeroCurve,
    bond_yield,
    bootstrap_credit_curve,
)

_MAX_GRID_MONTHS = 720


@dataclass
class CreditContext:
    """Everything a credit-curve calculation produces, for JSON or Excel output."""

    issuer_id: int
    issuer_name: str
    rate_curve_id: int
    recovery: float
    base: date
    trade: date
    discount: ZeroCurve
    result: CreditCurveResult
    quant_bonds: list[QuantBond]
    meta: list[dict[str, Any]]
    skipped: list[dict[str, str]]


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
    session: Session, client: MoexClient, isin: str, trade_date: date, force: bool = False
) -> MarketData:
    if not force:
        cached = repositories.get_market_data(session, isin, trade_date)
        if cached is not None and cached.is_priced:
            return cached
    quote = client.quote(isin, trade_date)
    return repositories.upsert_market_data(session, isin, quote)


def _effective_clean(md: MarketData | None) -> float | None:
    if md is None:
        return None
    return md.override_clean if md.override_clean is not None else md.clean_price


def price_row(bond: Bond, md: MarketData | None, trade_date: date) -> dict[str, Any]:
    eff_mat = bond.offer_date or bond.maturity_date
    years = round((eff_mat - trade_date).days / 365.0, 4) if eff_mat else None
    return {
        "isin": bond.isin,
        "name": bond.name or bond.shortname or bond.isin,
        "maturity_date": eff_mat.isoformat() if eff_mat else None,
        "years": years,
        "source": md.source if md else "unavailable",
        "fetched_clean": md.clean_price if md else None,
        "override_clean": md.override_clean if md else None,
        "effective_clean": _effective_clean(md),
        "accrued": md.accrued if md else None,
        "weight": round(md.weight, 4) if md else 0.0,
        "included": bool(md.included) if md else True,
        "is_priced": bool(md.is_priced) if md else False,
    }


def issuer_prices(
    session: Session,
    client: MoexClient,
    issuer_id: int,
    trade_date: date,
    force: bool = False,
) -> dict[str, Any]:
    if repositories.get_issuer(session, issuer_id) is None:
        raise KeyError(f"Unknown issuer {issuer_id}")
    rows: list[dict[str, Any]] = []
    for bond in repositories.list_bonds(session, issuer_id):
        try:
            md: MarketData | None = fetch_market(
                session, client, bond.isin, trade_date, force=force
            )
        except Exception:  # noqa: BLE001 - MOEX failure -> row shows unavailable
            md = None
        rows.append(price_row(bond, md, trade_date))
    rows.sort(key=lambda r: (r["years"] is None, r["years"] or 0.0))
    return {"trade_date": trade_date.isoformat(), "prices": rows}


def set_override(
    session: Session,
    isin: str,
    trade_date: date,
    override_clean: float | None,
    included: bool,
) -> dict[str, Any]:
    record = repositories.set_price_override(
        session, isin, trade_date, override_clean, included
    )
    bond = repositories.get_bond(session, isin)
    if bond is None:
        raise KeyError(f"Unknown bond {isin}")
    return price_row(bond, record, trade_date)


def compute_credit(
    session: Session,
    client: MoexClient,
    issuer_id: int,
    rate_curve_id: int,
    trade_date: date | None,
) -> CreditContext:
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
        if not md.included:
            skipped.append({"isin": bond.isin, "reason": "excluded"})
            continue
        eff_clean = _effective_clean(md)
        if eff_clean is None or md.face_value is None:
            skipped.append({"isin": bond.isin, "reason": "no usable price"})
            continue
        dirty = eff_clean / 100.0 * md.face_value + (md.accrued or 0.0)
        quant_bonds.append(quant)
        dirty_prices.append(dirty)
        weights.append(md.weight or 1.0)
        meta.append(
            {
                "isin": bond.isin,
                "name": bond.name or bond.shortname or bond.isin,
                "source": md.source,
                "market_clean": eff_clean,
                "market_dirty": dirty,
                "accrued": md.accrued or 0.0,
                "face": md.face_value or bond.face_value,
            }
        )

    if not quant_bonds:
        raise ValueError("No priced bonds available for this issuer / trade date")

    result = bootstrap_credit_curve(
        quant_bonds, dirty_prices, discount, issuer.recovery_rate, weights
    )
    return CreditContext(
        issuer_id=issuer_id,
        issuer_name=issuer.name,
        rate_curve_id=rate_curve_id,
        recovery=issuer.recovery_rate,
        base=base,
        trade=trade,
        discount=discount,
        result=result,
        quant_bonds=quant_bonds,
        meta=meta,
        skipped=skipped,
    )


def credit_curve(
    session: Session,
    client: MoexClient,
    issuer_id: int,
    rate_curve_id: int,
    trade_date: date | None,
) -> dict[str, Any]:
    ctx = compute_credit(session, client, issuer_id, rate_curve_id, trade_date)
    base, recovery = ctx.base, ctx.recovery

    def _yield_pct(quant: QuantBond, dirty: float) -> float | None:
        value = bond_yield(quant, dirty, base) * 100.0
        return round(value, 4) if math.isfinite(value) else None

    fits = []
    for info, fit, quant in zip(ctx.meta, ctx.result.fits, ctx.quant_bonds, strict=True):
        face = info["face"] or 1.0
        market_clean = info["market_clean"]
        model_clean = (fit.model_dirty - info["accrued"]) / face * 100.0
        residual_pct = (
            (model_clean - market_clean) / market_clean * 100.0 if market_clean else 0.0
        )
        fits.append(
            {
                "isin": info["isin"],
                "name": info["name"],
                "source": info["source"],
                "maturity_date": fit.effective_maturity.isoformat(),
                "years": fit.years,
                "market_clean": round(market_clean, 4),
                "market_yield": _yield_pct(quant, fit.market_dirty),
                "model_clean": round(model_clean, 4),
                "model_yield": _yield_pct(quant, fit.model_dirty),
                "residual_pct": round(residual_pct, 4),
                "weight": round(fit.weight, 4),
            }
        )
    fits.sort(key=lambda r: r["years"])

    curve = ctx.result.curve
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
        "issuer_id": ctx.issuer_id,
        "issuer_name": ctx.issuer_name,
        "rate_curve_id": ctx.rate_curve_id,
        "base_date": base.isoformat(),
        "trade_date": ctx.trade.isoformat(),
        "recovery_rate": recovery,
        "smoothing_lambda": ctx.result.smoothing_lambda,
        "curve": nodes,
        "fits": fits,
        "skipped": ctx.skipped,
        "hazard_export": export,
    }
