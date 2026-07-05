from __future__ import annotations

from datetime import date
from enum import StrEnum


class DayCount(StrEnum):
    """Supported day-count conventions."""

    ACT_360 = "ACT/360"
    ACT_365F = "ACT/365F"
    ACT_ACT_ISDA = "ACT/ACT ISDA"


def _days_in_year(year: int) -> int:
    return 366 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 365


def year_fraction(convention: DayCount, start: date, end: date) -> float:
    """Return the accrual year fraction between ``start`` and ``end``."""
    days = (end - start).days
    match convention:
        case DayCount.ACT_360:
            return days / 360.0
        case DayCount.ACT_365F:
            return days / 365.0
        case DayCount.ACT_ACT_ISDA:
            if start.year == end.year:
                return days / _days_in_year(start.year)
            first_year_days = (date(start.year, 12, 31) - start).days + 1
            last_year_days = (end - date(end.year, 1, 1)).days
            return first_year_days / _days_in_year(start.year) + last_year_days / _days_in_year(
                end.year
            ) + float(end.year - start.year - 1)
