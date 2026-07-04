from __future__ import annotations

from datetime import date

from cdslib import BusinessDayConvention, CalendarRegistry, HolidayCalendar


def _rub() -> HolidayCalendar:
    return CalendarRegistry.load_default().get("RUB")


def test_weekend_is_not_business_day() -> None:
    cal = HolidayCalendar("XXX", frozenset())
    assert cal.is_business_day(date(2024, 6, 8)) is False  # Saturday
    assert cal.is_business_day(date(2024, 6, 7)) is True  # Friday


def test_rub_holiday_loaded() -> None:
    cal = _rub()
    assert cal.is_business_day(date(2024, 6, 12)) is False  # Russia Day


def test_add_business_days_skips_holiday() -> None:
    cal = _rub()
    # 2024-06-11 (Tue) + 1 business day skips 06-12 (holiday) to 06-13 (Thu).
    assert cal.add_business_days(date(2024, 6, 11), 1) == date(2024, 6, 13)


def test_modified_following_rolls_back_across_month_end() -> None:
    cal = HolidayCalendar("XXX", frozenset())
    # 2024-03-31 is a Sunday; following would jump to April, so MF rolls back.
    result = cal.adjust(date(2024, 3, 31), BusinessDayConvention.MODIFIED_FOLLOWING)
    assert result == date(2024, 3, 29)  # Friday
