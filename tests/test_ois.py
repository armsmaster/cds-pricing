from __future__ import annotations

from datetime import date

import pytest

from cdslib import DayCount, UnknownRateIndex, generate_ois


def test_ruonia_5y_structure() -> None:
    swap = generate_ois("RUONIA", "5y", date(2024, 6, 3))

    assert swap.currency == "RUB"
    assert swap.day_count is DayCount.ACT_365F
    assert swap.rate_index == "RUONIA"
    # Spot = trade + 1 business day.
    assert swap.spot_date == date(2024, 6, 4)
    # Annual fixed leg over 5 years.
    assert len(swap.fixed_periods) == 5
    # Periods are contiguous and end at maturity.
    assert swap.fixed_periods[0].accrual_start == swap.spot_date
    assert swap.fixed_periods[-1].accrual_end == swap.maturity_date
    for earlier, later in zip(swap.fixed_periods, swap.fixed_periods[1:], strict=False):
        assert earlier.accrual_end == later.accrual_start
    # Each annual period is ~1 year and payment is on/after accrual end.
    for period in swap.fixed_periods:
        assert 0.99 < period.year_fraction < 1.02
        assert period.payment_date >= period.accrual_end


def test_short_tenor_is_single_period() -> None:
    swap = generate_ois("RUONIA", "3m", date(2024, 6, 3))
    assert len(swap.fixed_periods) == 1
    assert swap.fixed_periods[0].accrual_start == swap.spot_date


def test_case_insensitive_index_and_lowercase_tenor() -> None:
    swap = generate_ois("ruonia", "1w", date(2024, 6, 3))
    assert swap.rate_index == "RUONIA"
    assert len(swap.fixed_periods) == 1


def test_unknown_index_raises() -> None:
    with pytest.raises(UnknownRateIndex):
        generate_ois("NOPE", "1y", date(2024, 6, 3))
