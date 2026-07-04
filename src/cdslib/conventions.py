from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cdslib._data import load_json
from cdslib.calendar import BusinessDayConvention
from cdslib.daycount import DayCount
from cdslib.tenor import Tenor


class UnknownRateIndex(KeyError):
    """Raised when a rate index is not present in the registry."""


@dataclass(frozen=True)
class OISConvention:
    """Market conventions for the OIS attached to one overnight rate index."""

    rate_index: str
    currency: str
    day_count: DayCount
    spot_lag: int
    payment_lag: int
    fixed_frequency: Tenor
    business_day_convention: BusinessDayConvention


class RateIndexRegistry:
    """Maps each overnight rate index to its currency and OIS conventions."""

    def __init__(self, conventions: Mapping[str, OISConvention]) -> None:
        self._conventions = dict(conventions)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Mapping[str, Any]]) -> RateIndexRegistry:
        conventions = {}
        for name, spec in data.items():
            key = name.upper()
            conventions[key] = OISConvention(
                rate_index=key,
                currency=str(spec["currency"]).upper(),
                day_count=DayCount(str(spec["day_count"])),
                spot_lag=int(spec["spot_lag"]),
                payment_lag=int(spec["payment_lag"]),
                fixed_frequency=Tenor.parse(str(spec["fixed_frequency"])),
                business_day_convention=BusinessDayConvention(
                    str(spec["business_day_convention"])
                ),
            )
        return cls(conventions)

    @classmethod
    def load_default(cls) -> RateIndexRegistry:
        return cls.from_mapping(load_json("rate_indices.json"))

    def get(self, rate_index: str) -> OISConvention:
        key = rate_index.upper()
        try:
            return self._conventions[key]
        except KeyError:
            raise UnknownRateIndex(f"Unknown rate index: {rate_index!r}") from None

    def currency_of(self, rate_index: str) -> str:
        return self.get(rate_index).currency

    def indices(self) -> tuple[str, ...]:
        return tuple(self._conventions)
