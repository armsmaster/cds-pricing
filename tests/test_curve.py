from __future__ import annotations

import math
from datetime import date

from cdslib import CurvePoint, ZeroCurve


def test_base_date_discount_is_one() -> None:
    curve = ZeroCurve(date(2024, 1, 1), [CurvePoint(365, 0.10)])
    assert curve.discount_factor_days(0) == 1.0
    assert curve.discount_factor(date(2024, 1, 1)) == 1.0


def test_node_discount_and_zero_roundtrip() -> None:
    curve = ZeroCurve(date(2024, 1, 1), [CurvePoint(365, 0.10), CurvePoint(730, 0.12)])
    assert abs(curve.discount_factor_days(365) - math.exp(-0.10)) < 1e-12
    assert abs(curve.zero_rate_days(365) - 0.10) < 1e-12
    assert abs(curve.zero_rate_days(730) - 0.12) < 1e-12


def test_log_linear_interpolation_flat_between_equal_nodes() -> None:
    curve = ZeroCurve(date(2024, 1, 1), [CurvePoint(100, 0.10), CurvePoint(200, 0.10)])
    assert abs(curve.zero_rate_days(150) - 0.10) < 1e-9


def test_discount_factors_are_monotone() -> None:
    curve = ZeroCurve(date(2024, 1, 1), [CurvePoint(365, 0.10), CurvePoint(730, 0.13)])
    dfs = [curve.discount_factor_days(d) for d in range(0, 731, 30)]
    assert all(earlier >= later for earlier, later in zip(dfs, dfs[1:], strict=False))
