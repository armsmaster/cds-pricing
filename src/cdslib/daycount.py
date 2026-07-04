from __future__ import annotations

from datetime import date
from enum import StrEnum


class DayCount(StrEnum):
    """Supported day-count conventions."""

    ACT_360 = "ACT/360"
    ACT_365F = "ACT/365F"


def year_fraction(convention: DayCount, start: date, end: date) -> float:
    """Return the accrual year fraction between ``start`` and ``end``."""
    days = (end - start).days
    match convention:
        case DayCount.ACT_360:
            return days / 360.0
        case DayCount.ACT_365F:
            return days / 365.0
