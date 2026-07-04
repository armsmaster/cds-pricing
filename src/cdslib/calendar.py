from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from cdslib._data import load_json


class BusinessDayConvention(StrEnum):
    """How to roll a date that falls on a non-business day."""

    FOLLOWING = "FOLLOWING"
    MODIFIED_FOLLOWING = "MODIFIED_FOLLOWING"
    PRECEDING = "PRECEDING"


@dataclass(frozen=True)
class HolidayCalendar:
    """Weekend + holiday calendar for a single currency."""

    currency: str
    holidays: frozenset[date]

    def is_business_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def next_business_day(self, day: date) -> date:
        result = day
        while not self.is_business_day(result):
            result += timedelta(days=1)
        return result

    def previous_business_day(self, day: date) -> date:
        result = day
        while not self.is_business_day(result):
            result -= timedelta(days=1)
        return result

    def add_business_days(self, start: date, count: int) -> date:
        """Shift ``start`` by ``count`` business days (sign gives direction)."""
        if count == 0:
            return self.next_business_day(start)
        step = 1 if count > 0 else -1
        remaining = abs(count)
        result = start
        while remaining > 0:
            result += timedelta(days=step)
            if self.is_business_day(result):
                remaining -= 1
        return result

    def adjust(self, day: date, convention: BusinessDayConvention) -> date:
        """Roll ``day`` onto a business day per ``convention``."""
        if self.is_business_day(day):
            return day
        if convention is BusinessDayConvention.PRECEDING:
            return self.previous_business_day(day)
        rolled = self.next_business_day(day)
        if convention is BusinessDayConvention.MODIFIED_FOLLOWING and rolled.month != day.month:
            return self.previous_business_day(day)
        return rolled


class CalendarRegistry:
    """Holds a :class:`HolidayCalendar` per currency."""

    def __init__(self, calendars: Mapping[str, HolidayCalendar]) -> None:
        self._calendars = dict(calendars)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Iterable[str]]) -> CalendarRegistry:
        calendars = {
            currency.upper(): HolidayCalendar(
                currency=currency.upper(),
                holidays=frozenset(date.fromisoformat(d) for d in days),
            )
            for currency, days in data.items()
        }
        return cls(calendars)

    @classmethod
    def load_default(cls) -> CalendarRegistry:
        return cls.from_mapping(load_json("holidays.json"))

    def get(self, currency: str) -> HolidayCalendar:
        """Return the calendar for ``currency``; weekend-only if none is known."""
        key = currency.upper()
        return self._calendars.get(key, HolidayCalendar(key, frozenset()))
