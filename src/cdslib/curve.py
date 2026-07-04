from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt

Vector = npt.NDArray[np.float64]

DEFAULT_BASIS = 365.0


@dataclass(frozen=True)
class CurvePoint:
    """A single node of a zero curve."""

    tenor_days: int
    zero_rate: float  # continuously compounded, ACT/365 by default


class ZeroCurve:
    """Continuously-compounded zero curve with log-linear discount interpolation.

    The curve is anchored at ``base_date`` where the discount factor is 1. Nodes
    carry the zero rate for a tenor measured in calendar days; discount factors
    between nodes are log-linear (i.e. the integrated rate ``z * t`` is linear
    in time), which keeps forward rates piecewise constant between nodes.
    """

    def __init__(
        self,
        base_date: date,
        points: Sequence[CurvePoint],
        basis: float = DEFAULT_BASIS,
    ) -> None:
        ordered = sorted(points, key=lambda p: p.tenor_days)
        self.base_date = base_date
        self.basis = basis
        self._points: tuple[CurvePoint, ...] = tuple(ordered)
        days = [0.0] + [float(p.tenor_days) for p in ordered]
        integrated = [0.0] + [p.zero_rate * p.tenor_days / basis for p in ordered]
        self._days: Vector = np.asarray(days, dtype=np.float64)
        self._integrated_rate: Vector = np.asarray(integrated, dtype=np.float64)

    def points(self) -> tuple[CurvePoint, ...]:
        return self._points

    def as_tuples(self) -> list[tuple[int, float]]:
        return [(p.tenor_days, p.zero_rate) for p in self._points]

    def _integrated(self, days: float) -> float:
        return float(np.interp(days, self._days, self._integrated_rate))

    def discount_factor_days(self, days: float) -> float:
        return float(np.exp(-self._integrated(days)))

    def discount_factor(self, when: date) -> float:
        return self.discount_factor_days((when - self.base_date).days)

    def zero_rate_days(self, days: float) -> float:
        if days <= 0:
            return 0.0
        return self._integrated(days) / (days / self.basis)

    def zero_rate(self, when: date) -> float:
        return self.zero_rate_days((when - self.base_date).days)
