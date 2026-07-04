from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from cdslib.calendar import CalendarRegistry
from cdslib.conventions import OISConvention, RateIndexRegistry
from cdslib.daycount import DayCount
from cdslib.schedule import SchedulePeriod, generate_schedule
from cdslib.tenor import Tenor


@dataclass(frozen=True)
class OISSwap:
    """A dated OIS produced from a generic (rate index + tenor) specification.

    The floating leg mirrors the fixed schedule, so only the fixed-leg periods
    are stored; ``fixed_rate`` is left unset until a market quote is attached.
    """

    rate_index: str
    currency: str
    tenor: Tenor
    trade_date: date
    spot_date: date
    maturity_date: date
    day_count: DayCount
    fixed_periods: tuple[SchedulePeriod, ...]
    notional: float = 1.0
    fixed_rate: float | None = None


class OISGenerator:
    """Generates dated :class:`OISSwap` instances from stored conventions."""

    def __init__(
        self, indices: RateIndexRegistry, calendars: CalendarRegistry
    ) -> None:
        self._indices = indices
        self._calendars = calendars

    @classmethod
    def load_default(cls) -> OISGenerator:
        """Build a generator from the bundled rate-index and holiday data."""
        return cls(RateIndexRegistry.load_default(), CalendarRegistry.load_default())

    @property
    def indices(self) -> RateIndexRegistry:
        return self._indices

    @property
    def calendars(self) -> CalendarRegistry:
        return self._calendars

    def generate(
        self,
        rate_index: str,
        tenor: str | Tenor,
        trade_date: date | None = None,
    ) -> OISSwap:
        """Materialise the OIS for ``rate_index`` and ``tenor`` on ``trade_date``.

        ``trade_date`` defaults to today. The spot (effective) date is the
        trade date rolled forward by the index spot lag; the maturity is the
        spot date plus the tenor, business-day adjusted.
        """
        convention: OISConvention = self._indices.get(rate_index)
        calendar = self._calendars.get(convention.currency)
        tenor_obj = Tenor.parse(tenor) if isinstance(tenor, str) else tenor
        trade = trade_date if trade_date is not None else date.today()

        spot = calendar.add_business_days(trade, convention.spot_lag)
        maturity_unadjusted = tenor_obj.add_to(spot)
        periods = generate_schedule(
            effective=spot,
            maturity_unadjusted=maturity_unadjusted,
            frequency=convention.fixed_frequency,
            calendar=calendar,
            convention=convention.business_day_convention,
            day_count=convention.day_count,
            payment_lag=convention.payment_lag,
        )
        return OISSwap(
            rate_index=convention.rate_index,
            currency=convention.currency,
            tenor=tenor_obj,
            trade_date=trade,
            spot_date=spot,
            maturity_date=periods[-1].accrual_end,
            day_count=convention.day_count,
            fixed_periods=tuple(periods),
        )
