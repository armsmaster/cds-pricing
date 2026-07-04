"""cdslib — a minimal library for CDS pricing.

Current scope: term-structure tooling (tenors, day counts, holiday calendars,
schedules), data-driven market conventions, the generic OIS instrument and OIS
zero-curve bootstrap, plus survival (hazard) curves and a reduced-form bond
pricer used to bootstrap issuer credit curves from bond prices.
"""

from __future__ import annotations

from datetime import date

from cdslib.bond import Bond, BondCashflow, accrued_interest, price_bond
from cdslib.bootstrap import (
    BootstrapResult,
    InstrumentFit,
    OISQuote,
    bootstrap,
)
from cdslib.calendar import BusinessDayConvention, CalendarRegistry, HolidayCalendar
from cdslib.conventions import OISConvention, RateIndexRegistry, UnknownRateIndex
from cdslib.credit_bootstrap import (
    BondPriceFit,
    CreditCurveResult,
    bootstrap_credit_curve,
)
from cdslib.curve import CurvePoint, ZeroCurve
from cdslib.daycount import DayCount, year_fraction
from cdslib.ois import OISGenerator, OISSwap
from cdslib.schedule import SchedulePeriod, generate_schedule
from cdslib.survival import HazardPoint, SurvivalCurve
from cdslib.tenor import Tenor, TenorUnit

__all__ = [
    "Bond",
    "BondCashflow",
    "BondPriceFit",
    "BootstrapResult",
    "BusinessDayConvention",
    "CalendarRegistry",
    "CreditCurveResult",
    "CurvePoint",
    "DayCount",
    "HazardPoint",
    "HolidayCalendar",
    "InstrumentFit",
    "OISConvention",
    "OISGenerator",
    "OISQuote",
    "OISSwap",
    "RateIndexRegistry",
    "SchedulePeriod",
    "SurvivalCurve",
    "Tenor",
    "TenorUnit",
    "UnknownRateIndex",
    "ZeroCurve",
    "accrued_interest",
    "bootstrap",
    "bootstrap_credit_curve",
    "generate_ois",
    "generate_schedule",
    "price_bond",
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
