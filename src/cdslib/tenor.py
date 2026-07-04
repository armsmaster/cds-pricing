from __future__ import annotations

import calendar as _cal
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

_TENOR_RE = re.compile(r"^(\d+)([DWMY])$")


class TenorUnit(StrEnum):
    """Calendar unit used to express a tenor."""

    DAY = "D"
    WEEK = "W"
    MONTH = "M"
    YEAR = "Y"


def _add_months(start: date, months: int) -> date:
    """Add (possibly negative) whole months, clamping to end of month."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    last_day = _cal.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day))


@dataclass(frozen=True)
class Tenor:
    """A period length such as ``3M`` or ``5Y`` (a.k.a. tenor code)."""

    amount: int
    unit: TenorUnit

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError(f"Tenor amount must be positive, got {self.amount}")

    @classmethod
    def parse(cls, code: str) -> Tenor:
        """Parse a compact tenor code such as ``"1w"`` / ``"3M"`` / ``"5y"``."""
        match = _TENOR_RE.match(code.strip().upper())
        if match is None:
            raise ValueError(f"Invalid tenor code: {code!r}")
        return cls(int(match.group(1)), TenorUnit(match.group(2)))

    def add_to(self, start: date) -> date:
        """Return ``start`` shifted forward by this tenor (calendar-unadjusted)."""
        match self.unit:
            case TenorUnit.DAY:
                return start + timedelta(days=self.amount)
            case TenorUnit.WEEK:
                return start + timedelta(weeks=self.amount)
            case TenorUnit.MONTH:
                return _add_months(start, self.amount)
            case TenorUnit.YEAR:
                return _add_months(start, self.amount * 12)

    def subtract_from(self, start: date) -> date:
        """Return ``start`` shifted backward by this tenor (calendar-unadjusted)."""
        match self.unit:
            case TenorUnit.DAY:
                return start - timedelta(days=self.amount)
            case TenorUnit.WEEK:
                return start - timedelta(weeks=self.amount)
            case TenorUnit.MONTH:
                return _add_months(start, -self.amount)
            case TenorUnit.YEAR:
                return _add_months(start, -self.amount * 12)

    def __str__(self) -> str:
        return f"{self.amount}{self.unit.value}"
