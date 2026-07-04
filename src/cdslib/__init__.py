"""cdslib — a minimal library for CDS pricing.

Current scope: term-structure tooling (tenors, day counts, holiday calendars,
schedules), data-driven market conventions, and the generic OIS instrument used
to bootstrap interest-rate curves.
"""

from __future__ import annotations

from datetime import date

from cdslib.bootstrap import (
    BootstrapResult,
    InstrumentFit,
    OISQuote,
    bootstrap,
)
from cdslib.calendar import BusinessDayConvention, CalendarRegistry, HolidayCalendar
from cdslib.conventions import OISConvention, RateIndexRegistry, UnknownRateIndex
from cdslib.curve import CurvePoint, ZeroCurve
from cdslib.daycount import DayCount, year_fraction
from cdslib.ois import OISGenerator, OISSwap
from cdslib.schedule import SchedulePeriod, generate_schedule
from cdslib.tenor import Tenor, TenorUnit

__all__ = [
    "BootstrapResult",
    "BusinessDayConvention",
    "CalendarRegistry",
    "CurvePoint",
    "DayCount",
    "HolidayCalendar",
    "InstrumentFit",
    "OISConvention",
    "OISGenerator",
    "OISQuote",
    "OISSwap",
    "RateIndexRegistry",
    "SchedulePeriod",
    "Tenor",
    "TenorUnit",
    "UnknownRateIndex",
    "ZeroCurve",
    "bootstrap",
    "generate_ois",
    "generate_schedule",
    "year_fraction",
]

_default_generator: OISGenerator | None = None


def generate_ois(
    rate_index: str, tenor: str, trade_date: date | None = None
) -> OISSwap:
    """Generate an OIS using the bundled default conventions and calendars."""
    global _default_generator
    if _default_generator is None:
        _default_generator = OISGenerator.load_default()
    return _default_generator.generate(rate_index, tenor, trade_date)
