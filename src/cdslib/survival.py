from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt

Vector = npt.NDArray[np.float64]

DEFAULT_BASIS = 365.0


@dataclass(frozen=True)
class HazardPoint:
    """A single node of a survival curve."""

    tenor_days: int
    hazard: float  # continuously-compounded average hazard to this tenor, ACT/365


class SurvivalCurve:
    """Survival-probability curve driven by a piecewise-constant hazard rate.

    Anchored at ``base_date`` where the survival probability is 1. Nodes carry
    the *average* hazard from the base date to the tenor; the integrated hazard
    ``H(t) = hazard * t`` is interpolated linearly in time (so the instantaneous
    forward hazard is piecewise constant), and survival is ``Q(t) = exp(-H(t))``.
    """

    def __init__(
        self,
        base_date: date,
        points: Sequence[HazardPoint],
        basis: float = DEFAULT_BASIS,
    ) -> None:
        ordered = sorted(points, key=lambda p: p.tenor_days)
        self.base_date = base_date
        self.basis = basis
        self._points: tuple[HazardPoint, ...] = tuple(ordered)
        days = [0.0] + [float(p.tenor_days) for p in ordered]
        integrated = [0.0] + [p.hazard * p.tenor_days / basis for p in ordered]
        self._days: Vector = np.asarray(days, dtype=np.float64)
        self._integrated_hazard: Vector = np.asarray(integrated, dtype=np.float64)

    def points(self) -> tuple[HazardPoint, ...]:
        return self._points

    def as_tuples(self) -> list[tuple[int, float]]:
        return [(p.tenor_days, p.hazard) for p in self._points]

    def _integrated(self, days: float) -> float:
        return float(np.interp(days, self._days, self._integrated_hazard))

    def survival_days(self, days: float) -> float:
        if days <= 0:
            return 1.0
        return float(np.exp(-self._integrated(days)))

    def survival(self, when: date) -> float:
        return self.survival_days((when - self.base_date).days)

    def default_probability(self, when: date) -> float:
        return 1.0 - self.survival(when)

    def hazard_days(self, days: float) -> float:
        if days <= 0:
            return 0.0
        return self._integrated(days) / (days / self.basis)

    def hazard(self, when: date) -> float:
        return self.hazard_days((when - self.base_date).days)
