from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from cdslib import OISGenerator, OISQuote, bootstrap

TRADE = date(2024, 6, 3)
_QUOTES = [
    ("1m", 14.20, 14.60),
    ("3m", 14.10, 14.55),
    ("6m", 13.95, 14.45),
    ("1y", 13.80, 14.35),
    ("2y", 13.50, 14.20),
    ("3y", 13.30, 14.10),
    ("5y", 13.00, 13.90),
]


def _quotes() -> list[OISQuote]:
    return [OISQuote("RUONIA", tenor, bid, ask) for tenor, bid, ask in _QUOTES]


def _forward_curve(result: object) -> np.ndarray:
    tuples = result.as_tuples()  # type: ignore[attr-defined]
    days = np.array([0.0] + [d for d, _ in tuples])
    integrated = np.array([0.0] + [d / 365.0 * r for d, r in tuples])
    return np.diff(integrated) / np.diff(days / 365.0)


def test_monthly_grid_structure() -> None:
    result = bootstrap(_quotes(), TRADE)
    days = [p.tenor_days for p in result.points]
    assert 58 <= len(days) <= 62  # 5y on a monthly grid
    assert days == sorted(days)
    assert len(set(days)) == len(days)
    assert 25 <= days[0] <= 35
    assert all(0.05 < p.zero_rate < 0.30 for p in result.points)


def test_average_adjustment_within_default_budget() -> None:
    result = bootstrap(_quotes(), TRADE)
    assert result.average_adjustment_bps <= 15.0 + 1e-6


def test_smooth_inputs_give_non_humpy_forward() -> None:
    # A smoothly-sloped set of mids must produce a monotone forward curve
    # (no overshoot/humps). Allow a single tiny direction change for slack.
    forward = _forward_curve(bootstrap(_quotes(), TRADE))
    direction_changes = int(np.sum(np.diff(np.sign(np.diff(forward))) != 0))
    assert direction_changes <= 1


def test_wide_spread_quote_absorbs_more_adjustment() -> None:
    # Reliability weighting: a wide bid/ask (unreliable) outlier should be moved
    # far more than its tight, trustworthy neighbours.
    quotes = [
        OISQuote("RUONIA", "1y", 14.00, 14.10),
        OISQuote("RUONIA", "2y", 14.00, 14.10),
        OISQuote("RUONIA", "3y", 13.20, 14.40),  # 120 bps spread, anomalous mid
        OISQuote("RUONIA", "4y", 14.00, 14.10),
        OISQuote("RUONIA", "5y", 14.00, 14.10),
    ]
    fits = {f.tenor: abs(f.adjustment_bps) for f in bootstrap(quotes, TRADE).fits}
    assert fits["3y"] > fits["1y"]
    assert fits["3y"] > fits["5y"]


def test_custom_budget_caps_adjustment() -> None:
    loose = bootstrap(_quotes(), TRADE)
    tight = bootstrap(_quotes(), TRADE, max_avg_adjustment_bps=1.0)
    assert tight.average_adjustment_bps <= 1.0 + 1e-6
    assert tight.average_adjustment_bps <= loose.average_adjustment_bps


def test_curve_reprices_reported_model_rates() -> None:
    result = bootstrap(_quotes(), TRADE)
    generator = OISGenerator.load_default()
    curve = result.curve
    for quote, fit in zip(_quotes(), result.fits, strict=True):
        swap = generator.generate(quote.rate_index, quote.tenor, TRADE)
        annuity = sum(
            p.year_fraction * curve.discount_factor(p.accrual_end)
            for p in swap.fixed_periods
        )
        par = (1.0 - curve.discount_factor(swap.maturity_date)) / annuity
        assert abs(par - fit.model_rate) < 1e-9


def test_curve_discount_factors_monotone() -> None:
    result = bootstrap(_quotes(), TRADE)
    curve = result.curve
    last = result.points[-1].tenor_days
    dfs = [curve.discount_factor_days(d) for d in range(0, last + 1, 30)]
    assert dfs[0] == 1.0
    assert all(earlier >= later for earlier, later in zip(dfs, dfs[1:], strict=False))


def test_deterministic() -> None:
    first = bootstrap(_quotes(), TRADE)
    second = bootstrap(_quotes(), TRADE)
    assert [p.zero_rate for p in first.points] == [p.zero_rate for p in second.points]


def test_empty_quotes_rejected() -> None:
    with pytest.raises(ValueError):
        bootstrap([], TRADE)


def test_mixed_rate_indices_rejected() -> None:
    with pytest.raises(ValueError):
        bootstrap(
            [OISQuote("RUONIA", "1y", 13.0, 14.0), OISQuote("SOFR", "1y", 5.0, 6.0)],
            TRADE,
        )
