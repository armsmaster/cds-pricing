from __future__ import annotations

import math
from datetime import date, timedelta

from cdslib import HazardPoint, SurvivalCurve


def _curve() -> SurvivalCurve:
    base = date(2024, 1, 1)
    return SurvivalCurve(base, [HazardPoint(365, 0.02), HazardPoint(730, 0.03)])


def test_survival_is_one_at_base() -> None:
    curve = _curve()
    assert curve.survival_days(0) == 1.0
    assert curve.survival(date(2024, 1, 1)) == 1.0


def test_survival_matches_hazard() -> None:
    curve = _curve()
    assert abs(curve.survival_days(365) - math.exp(-0.02)) < 1e-12
    assert abs(curve.survival_days(730) - math.exp(-0.06)) < 1e-12
    assert abs(curve.hazard_days(365) - 0.02) < 1e-12


def test_survival_monotone_decreasing() -> None:
    curve = _curve()
    base = curve.base_date
    values = [curve.survival(base + timedelta(days=d)) for d in range(0, 731, 30)]
    assert all(a >= b for a, b in zip(values, values[1:], strict=False))
    when = date(2025, 6, 1)
    assert abs(curve.default_probability(when) - (1 - curve.survival(when))) < 1e-15
