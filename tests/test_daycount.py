from __future__ import annotations

from datetime import date

from cdslib import DayCount, year_fraction


def test_act_360() -> None:
    yf = year_fraction(DayCount.ACT_360, date(2024, 1, 1), date(2024, 1, 31))
    assert yf == 30 / 360


def test_act_365f() -> None:
    yf = year_fraction(DayCount.ACT_365F, date(2024, 1, 1), date(2025, 1, 1))
    assert yf == 366 / 365
