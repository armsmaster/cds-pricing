from __future__ import annotations

from datetime import date

import pytest

from cdslib import (
    CurvePoint,
    DayCount,
    HazardPoint,
    HolidayCalendar,
    SurvivalCurve,
    ZeroCurve,
    cds_breakdown,
    cds_credit_dv01,
    cds_dv01,
    generate_cds_schedule,
    price_cds,
    year_fraction,
)

BASE = date(2024, 6, 3)
_EMPTY = HolidayCalendar("XXX", frozenset())


def _discount() -> ZeroCurve:
    return ZeroCurve(BASE, [CurvePoint(3650, 0.14)])


def _survival() -> SurvivalCurve:
    return SurvivalCurve(BASE, [HazardPoint(3650, 0.03)])


def test_schedule_generates_standard_1y_quarters() -> None:
    schedule = generate_cds_schedule(BASE, 12, _EMPTY)
    assert len(schedule.periods) >= 3  # first is a residual stub
    assert schedule.periods[0].accrual_start == schedule.effective_date
    assert schedule.protection_start == date(2024, 6, 4)
    assert schedule.protection_end == date(2025, 3, 20)


def test_par_spread_with_zero_recovery_is_positive() -> None:
    schedule = generate_cds_schedule(BASE, 12, _EMPTY)
    result = price_cds(schedule, _discount(), _survival(), 0.0)
    assert result["par_spread"] > 0
    assert result["par_spread"] < 0.10  # reasonable


def test_par_spread_decreases_with_recovery() -> None:
    schedule = generate_cds_schedule(BASE, 12, _EMPTY)
    low = price_cds(schedule, _discount(), _survival(), 0.2)
    high = price_cds(schedule, _discount(), _survival(), 0.6)
    assert high["par_spread"] < low["par_spread"]


def test_net_premium_is_negative_when_spread_below_coupon() -> None:
    schedule = generate_cds_schedule(BASE, 12, _EMPTY)
    # 1% hazard × 60% LGD = ~60 bp spread – below the 100 bp coupon.
    surv = SurvivalCurve(BASE, [HazardPoint(3650, 0.01)])
    result = price_cds(schedule, _discount(), surv, 0.4)
    assert result["par_spread"] < 0.01
    assert result["net_premium"] < 0


def test_breakdown_has_one_row_per_period() -> None:
    schedule = generate_cds_schedule(BASE, 12, _EMPTY)
    rows = cds_breakdown(schedule, _discount(), _survival(), 0.4)
    assert len(rows) == len(schedule.periods)
    for key in ("date", "year_fraction", "df", "survival", "premium_pv", "protection_pv"):
        assert key in rows[0]


def test_dv01_returns_nonnegative() -> None:
    schedule = generate_cds_schedule(BASE, 12, _EMPTY)
    assert cds_dv01(schedule, _discount(), _survival(), 0.4) >= 0
    assert cds_credit_dv01(schedule, _discount(), _survival(), 0.4) >= 0


def test_par_spread_roundtrip() -> None:
    schedule = generate_cds_schedule(BASE, 12, _EMPTY)
    result = price_cds(schedule, _discount(), _survival(), 0.4)
    assert result["upfront"] == pytest.approx((result["par_spread"] - 0.01) * result["rpv01"])


def test_act_act_isda_single_year() -> None:
    assert year_fraction(DayCount.ACT_ACT_ISDA, date(2024, 1, 1), date(2025, 1, 1)) == pytest.approx(1.0)


def test_act_act_isda_cross_leap_year() -> None:
    got = year_fraction(DayCount.ACT_ACT_ISDA, date(2023, 12, 31), date(2025, 1, 1))
    assert got == pytest.approx(1.0 + 1 / 365)
