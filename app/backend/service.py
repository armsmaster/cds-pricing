from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.backend.schemas import QuoteIn
from cdslib import (
    BootstrapResult,
    CalendarRegistry,
    OISGenerator,
    OISQuote,
    RateIndexRegistry,
    bootstrap,
)

_generator: OISGenerator | None = None


def _get_generator(session: Session | None = None) -> OISGenerator:
    global _generator
    if session is not None:
        from app.backend.repositories import build_calendar_mapping, build_rate_index_mapping

        rates = build_rate_index_mapping(session)
        holidays = build_calendar_mapping(session)
        return OISGenerator(
            RateIndexRegistry.from_mapping(rates),
            CalendarRegistry.from_mapping(holidays),
        )
    if _generator is None:
        _generator = OISGenerator.load_default()
    return _generator


def _to_quotes(items: list[QuoteIn]) -> list[OISQuote]:
    return [OISQuote(i.rate, i.tenor, i.bid, i.ask) for i in items]


def compute(
    items: list[QuoteIn],
    trade_date: date | None,
    max_adjustment_bps: float,
    generator: OISGenerator | None = None,
) -> BootstrapResult:
    """Run the OIS bootstrap and return the raw cdslib result."""
    trade = trade_date or date.today()
    gen = generator or _get_generator()
    return bootstrap(
        _to_quotes(items),
        trade,
        generator=gen,
        max_avg_adjustment_bps=max_adjustment_bps,
    )


def zcyc_dict(result: BootstrapResult) -> dict[str, float]:
    """ZCYC export: future date -> annual-compounded yield (decimal)."""
    return {
        (result.spot_date + timedelta(days=p.tenor_days)).isoformat(): round(
            math.exp(p.zero_rate) - 1.0, 8
        )
        for p in result.points
    }


def preview(
    items: list[QuoteIn], trade_date: date | None, generator: OISGenerator | None = None
) -> dict[str, Any]:
    """Par-rate preview: each quote's maturity plus its bid/ask/mid."""
    trade = trade_date or date.today()
    gen = generator or _get_generator()
    rows: list[dict[str, Any]] = []
    for quote in _to_quotes(items):
        swap = gen.generate(quote.rate_index, quote.tenor, trade)
        years = (swap.maturity_date - swap.spot_date).days / 365.0
        rows.append(
            {
                "tenor": quote.tenor,
                "maturity_date": swap.maturity_date.isoformat(),
                "years": round(years, 4),
                "bid": quote.bid,
                "ask": quote.ask,
                "mid": round(quote.mid_pct, 6),
            }
        )
    rows.sort(key=lambda r: r["years"])
    return {"trade_date": trade.isoformat(), "quotes": rows}


def _forward_curve(
    points: list[tuple[int, float]], spot: date
) -> list[dict[str, Any]]:
    days = [0.0] + [float(d) for d, _ in points]
    integrated = [0.0] + [d / 365.0 * z for d, z in points]
    times = [d / 365.0 for d in days]
    rows: list[dict[str, Any]] = []
    for i in range(1, len(days)):
        forward = (integrated[i] - integrated[i - 1]) / (times[i] - times[i - 1])
        node_date = spot + timedelta(days=int(days[i]))
        rows.append(
            {
                "date": node_date.isoformat(),
                "years": round(times[i], 4),
                "forward": round(forward * 100.0, 6),
            }
        )
    return rows


def run_bootstrap(
    items: list[QuoteIn],
    trade_date: date | None,
    max_adjustment_bps: float,
    generator: OISGenerator | None = None,
) -> dict[str, Any]:
    """Bootstrap the curve and package everything the UI needs."""
    result = compute(items, trade_date, max_adjustment_bps, generator)
    trade = result.trade_date
    quotes = _to_quotes(items)
    spot = result.spot_date
    gen = generator or _get_generator()

    curve: list[dict[str, Any]] = []
    for point in result.points:
        node_date = spot + timedelta(days=point.tenor_days)
        cont = point.zero_rate
        annual = math.exp(cont) - 1.0
        curve.append(
            {
                "date": node_date.isoformat(),
                "years": round(point.tenor_days / 365.0, 4),
                "zero_cont": round(cont * 100.0, 6),
                "zero_annual": round(annual * 100.0, 6),
            }
        )

    forwards = _forward_curve(
        [(p.tenor_days, p.zero_rate) for p in result.points], spot
    )

    fits: list[dict[str, Any]] = []
    for quote, fit in zip(quotes, result.fits, strict=True):
        swap = gen.generate(quote.rate_index, quote.tenor, trade)
        years = (swap.maturity_date - spot).days / 365.0
        fits.append(
            {
                "tenor": fit.tenor,
                "years": round(years, 4),
                "maturity_date": swap.maturity_date.isoformat(),
                "bid": quote.bid,
                "ask": quote.ask,
                "raw_mid": round(fit.mid_rate * 100.0, 6),
                "adj_mid": round(fit.model_rate * 100.0, 6),
                "adj_bps": round(fit.adjustment_bps, 4),
                "within_spread": fit.within_spread,
            }
        )
    fits.sort(key=lambda r: r["years"])

    return {
        "trade_date": trade.isoformat(),
        "spot_date": spot.isoformat(),
        "rate_index": result.rate_index,
        "average_adjustment_bps": round(result.average_adjustment_bps, 4),
        "smoothing_lambda": result.smoothing_lambda,
        "curve": curve,
        "forwards": forwards,
        "fits": fits,
        "zcyc": zcyc_dict(result),
    }
