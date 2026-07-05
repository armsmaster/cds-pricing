"""CDS pricing service: builds contracts from stored curves, caches results
and provides per-contract breakdowns for the detail view.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend import credit_service, repositories
from app.backend.models import CdsPriceCache
from app.backend.moex import MoexClient
from cdslib import (
    CalendarRegistry,
    HolidayCalendar,
    cds_breakdown,
    cds_credit_dv01,
    cds_dv01,
    generate_cds_schedule,
    price_cds,
)

_STANDARD_TENORS = {6, 12, 24, 36, 60, 84, 120}  # months
_MAX_TENOR = 10  # years

_calendar: HolidayCalendar | None = None


def _get_calendar() -> HolidayCalendar:
    global _calendar
    if _calendar is None:
        _calendar = CalendarRegistry.load_default().get("RUB")
    return _calendar


def _stale(
    session: Session,
    rate_curve_id: int,
    issuer_id: int,
    trade_date: date,
    cached_at: datetime,
) -> bool:
    rate = repositories.get_rate_curve(session, rate_curve_id)
    if rate is not None and rate.updated_at is not None and rate.updated_at > cached_at:
        return True
    credit_at = repositories.latest_credit_change(session, issuer_id)
    return credit_at > cached_at


def price_all_cds(
    session: Session,
    client: MoexClient,
    rate_curve_id: int,
    trade_date: date,
    issuer_id: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Price CDS for every issuer with credit data (or a single issuer).

    Tenors are generated quarterly from 3m up to the furthest credit-curve
    node, never exceeding _MAX_TENOR years.  Results are cached per (rate
    curve, issuer, trade date) and auto-invalidated when the issuer's bond
    data or the rate curve itself changes after the cache was written.
    """
    record = repositories.get_rate_curve(session, rate_curve_id)
    if record is None:
        raise KeyError(f"Unknown rate curve {rate_curve_id}")
    discount = repositories.to_zero_curve(record)
    base = discount.base_date
    calendar = _get_calendar()

    issuer_ids: list[int]
    if issuer_id is not None:
        issuer_ids = [issuer_id]
    else:
        issuer_ids = [i.id for i in repositories.list_issuers(session)]

    results: list[dict[str, Any]] = []
    cached_issuers = 0

    for iid in issuer_ids:
        if not refresh:
            entry = session.scalar(
                select(CdsPriceCache).where(
                    CdsPriceCache.rate_curve_id == rate_curve_id,
                    CdsPriceCache.issuer_id == iid,
                    CdsPriceCache.trade_date == trade_date,
                )
            )
            if entry is not None and not _stale(
                session, rate_curve_id, iid, trade_date, entry.cached_at
            ):
                results.extend(json.loads(entry.results_json))
                cached_issuers += 1
                continue

        try:
            ctx = credit_service.compute_credit(session, client, iid, rate_curve_id, trade_date)
        except (KeyError, ValueError):
            continue
        survival = ctx.result.curve
        recovery = ctx.recovery
        max_node = max(p.tenor_days for p in survival.points()) if survival.points() else 0

        issuer_rows: list[dict[str, Any]] = []
        for quarter in range(1, _MAX_TENOR * 4 + 1):
            tenor_months = quarter * 3
            m = base.month + (tenor_months % 12)
            y = base.year + tenor_months // 12
            if m > 12:
                m -= 12
                y += 1
            expiry = date(y, m, base.day)
            if (expiry - base).days <= 0:
                continue
            if (expiry - base).days > max_node:
                continue

            schedule = generate_cds_schedule(base, tenor_months, calendar)
            result = price_cds(schedule, discount, survival, recovery)
            breakdown = cds_breakdown(schedule, discount, survival, recovery)
            dv = cds_dv01(schedule, discount, survival, recovery)
            cdv = cds_credit_dv01(schedule, discount, survival, recovery)
            is_std = tenor_months in _STANDARD_TENORS

            issuer_rows.append(
                {
                    "issuer_id": iid,
                    "issuer_name": ctx.issuer_name,
                    "tenor_months": tenor_months,
                    "is_standard": is_std,
                    "expiry_date": schedule.protection_end.isoformat(),
                    "par_spread": round(result["par_spread"] * 1e4, 2),
                    "upfront": round(result["upfront"] * 100, 4),
                    "rebate": round(result["rebate"] * 100, 4),
                    "net_premium": round(result["net_premium"] * 100, 4),
                    "dv01": round(dv * 100, 6),
                    "credit_dv01": round(cdv * 100, 6),
                    "breakdown": breakdown,
                }
            )

        repositories.set_cds_cache(
            session, rate_curve_id, iid, trade_date, json.dumps(issuer_rows)
        )
        results.extend(issuer_rows)

    results.sort(key=lambda r: (r["issuer_name"], r["tenor_months"]))
    return {"results": results, "cached": cached_issuers}


def cds_detail(
    session: Session,
    client: MoexClient,
    issuer_id: int,
    rate_curve_id: int,
    trade_date: date,
    tenor_months: int,
) -> dict[str, Any]:
    """Full pricing breakdown for a single CDS contract."""
    record = repositories.get_rate_curve(session, rate_curve_id)
    if record is None:
        raise KeyError(f"Unknown rate curve {rate_curve_id}")
    discount = repositories.to_zero_curve(record)
    base = discount.base_date
    ctx = credit_service.compute_credit(session, client, issuer_id, rate_curve_id, trade_date)
    survival = ctx.result.curve
    recovery = ctx.recovery

    schedule = generate_cds_schedule(base, tenor_months, _get_calendar())
    result = price_cds(schedule, discount, survival, recovery)
    breakdown = cds_breakdown(schedule, discount, survival, recovery)

    return {
        "issuer_name": ctx.issuer_name,
        "tenor_months": tenor_months,
        "expiry_date": schedule.protection_end.isoformat(),
        "par_spread": round(result["par_spread"] * 1e4, 2),
        "upfront": round(result["upfront"] * 100, 4),
        "rebate": round(result["rebate"] * 100, 4),
        "net_premium": round(result["net_premium"] * 100, 4),
        "dv01": round(cds_dv01(schedule, discount, survival, recovery) * 100, 6),
        "credit_dv01": round(
            cds_credit_dv01(schedule, discount, survival, recovery) * 100, 6
        ),
        "protection_leg": round(result["protection_leg"], 8),
        "premium_leg": round(result["premium_leg"], 8),
        "rpv01": round(result["rpv01"], 8),
        "breakdown": breakdown,
    }
