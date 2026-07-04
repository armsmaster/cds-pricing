from __future__ import annotations

from datetime import date, timedelta

import pytest

from cdslib import (
    Bond,
    BondCashflow,
    CurvePoint,
    HazardPoint,
    SurvivalCurve,
    ZeroCurve,
    bootstrap_credit_curve,
    price_bond,
)

BASE = date(2024, 1, 1)


def _bullet(isin: str, coupon: float, years: int) -> Bond:
    flows = []
    for k in range(1, years + 1):
        principal = 1000.0 if k == years else 0.0
        flows.append(BondCashflow(BASE + timedelta(days=365 * k), coupon, principal))
    return Bond(isin, 1000.0, tuple(flows))


def _discount() -> ZeroCurve:
    return ZeroCurve(BASE, [CurvePoint(3650, 0.10)])


def test_recovers_flat_hazard_from_synthetic_prices() -> None:
    bonds = [_bullet(f"B{y}", 80.0, y) for y in (2, 3, 5, 7)]
    discount = _discount()
    recovery = 0.4
    true_hazard = 0.03
    true_curve = SurvivalCurve(
        BASE, [HazardPoint((b.effective_maturity - BASE).days, true_hazard) for b in bonds]
    )
    market = [price_bond(b, discount, true_curve, recovery) for b in bonds]

    result = bootstrap_credit_curve(bonds, market, discount, recovery)

    assert result.base_date == BASE
    assert result.recovery_rate == recovery
    # Reprices the synthetic market to near-zero residuals.
    assert max(abs(f.residual) for f in result.fits) < 1e-2
    # Recovers the flat hazard at each pillar.
    for _, hazard in result.curve.as_tuples():
        assert abs(hazard - true_hazard) < 5e-3


def test_survival_is_decreasing_and_positive() -> None:
    bonds = [_bullet(f"B{y}", 80.0, y) for y in (2, 4, 6)]
    discount = _discount()
    market = [
        price_bond(
            b,
            discount,
            SurvivalCurve(BASE, [HazardPoint((b.effective_maturity - BASE).days, 0.05)]),
            0.4,
        )
        for b in bonds
    ]
    result = bootstrap_credit_curve(bonds, market, discount, 0.4)
    curve = result.curve
    survivals = [curve.survival_days(d) for d in range(0, 365 * 6, 90)]
    assert all(0.0 < s <= 1.0 for s in survivals)
    assert all(a >= b for a, b in zip(survivals, survivals[1:], strict=False))


def test_reliability_weighting_favours_trusted_bonds() -> None:
    bonds = [_bullet(f"B{y}", 80.0, y) for y in (2, 3, 5)]
    discount = _discount()
    fair = [
        price_bond(
            b,
            discount,
            SurvivalCurve(BASE, [HazardPoint((b.effective_maturity - BASE).days, 0.03)]),
            0.4,
        )
        for b in bonds
    ]
    # Corrupt the middle bond's price but down-weight it heavily.
    market = list(fair)
    market[1] += 50.0
    result = bootstrap_credit_curve(bonds, market, discount, 0.4, weights=[1.0, 0.01, 1.0])
    fits = {f.isin: f for f in result.fits}
    # The trusted bonds stay well-fit; the distrusted one absorbs the mispricing.
    assert abs(fits["B2"].residual) < 5.0
    assert abs(fits["B5"].residual) < 5.0
    assert abs(fits["B3"].residual) > abs(fits["B2"].residual)


def test_errors() -> None:
    discount = _discount()
    with pytest.raises(ValueError):
        bootstrap_credit_curve([], [], discount, 0.4)
    with pytest.raises(ValueError):
        bootstrap_credit_curve([_bullet("B", 80.0, 3)], [900.0, 950.0], discount, 0.4)
