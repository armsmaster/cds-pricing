from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from cdslib.calendar import BusinessDayConvention, HolidayCalendar
from cdslib.daycount import DayCount, year_fraction
from cdslib.tenor import Tenor


@dataclass(frozen=True)
class SchedulePeriod:
    """A single accrual period of a swap leg."""

    accrual_start: date
    accrual_end: date
    payment_date: date
    year_fraction: float


def generate_schedule(
    effective: date,
    maturity_unadjusted: date,
    frequency: Tenor,
    calendar: HolidayCalendar,
    convention: BusinessDayConvention,
    day_count: DayCount,
    payment_lag: int,
) -> list[SchedulePeriod]:
    """Build accrual periods by rolling backward from maturity.

    Boundaries are generated on the unadjusted schedule then business-day
    adjusted; any front stub is placed at the start of the schedule.
    """
    boundaries = [maturity_unadjusted]
    cursor = maturity_unadjusted
    while True:
        previous = frequency.subtract_from(cursor)
        if previous <= effective:
            break
        boundaries.append(previous)
        cursor = previous
    boundaries.append(effective)
    boundaries.reverse()

    periods: list[SchedulePeriod] = []
    for start_unadjusted, end_unadjusted in zip(boundaries[:-1], boundaries[1:], strict=True):
        start = calendar.adjust(start_unadjusted, convention)
        end = calendar.adjust(end_unadjusted, convention)
        payment = calendar.add_business_days(end, payment_lag)
        periods.append(
            SchedulePeriod(
                accrual_start=start,
                accrual_end=end,
                payment_date=payment,
                year_fraction=year_fraction(day_count, start, end),
            )
        )
    return periods
