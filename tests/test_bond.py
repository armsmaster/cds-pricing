from __future__ import annotations

from datetime import date, timedelta

from cdslib import (
    Bond,
    BondCashflow,
    CurvePoint,
    HazardPoint,
    SurvivalCurve,
    ZeroCurve,
    accrued_interest,
    bond_yield,
    price_bond,
)

BASE = date(2024, 1, 1)


def _bullet(coupon: float, years: int) -> Bond:
    flows = []
    for k in range(1, years + 1):
        principal = 1000.0 if k == years else 0.0
        flows.append(BondCashflow(BASE + timedelta(days=365 * k), coupon, principal))
    return Bond("TEST", 1000.0, tuple(flows), first_accrual_date=BASE)


def _flat_discount(rate: float) -> ZeroCurve:
    return ZeroCurve(BASE, [CurvePoint(3650, rate)])


def _flat_survival(hazard: float, days: int = 3650) -> SurvivalCurve:
    return SurvivalCurve(BASE, [HazardPoint(days, hazard)])


def test_riskfree_price_equals_discounted_cashflows() -> None:
    bond = _bullet(80.0, 3)
    discount = _flat_discount(0.10)
    survival = _flat_survival(0.0)  # no default risk
    expected = sum(
        (cf.coupon + cf.principal) * discount.discount_factor(cf.date)
        for cf in bond.cashflows
    )
    assert abs(price_bond(bond, discount, survival, 0.4) - expected) < 1e-9


def test_higher_hazard_lowers_price() -> None:
    bond = _bullet(80.0, 5)
    discount = _flat_discount(0.10)
    safe = price_bond(bond, discount, _flat_survival(0.0), 0.4)
    risky = price_bond(bond, discount, _flat_survival(0.08), 0.4)
    assert risky < safe


def test_higher_recovery_raises_price() -> None:
    bond = _bullet(80.0, 5)
    discount = _flat_discount(0.10)
    survival = _flat_survival(0.08)
    low = price_bond(bond, discount, survival, 0.2)
    high = price_bond(bond, discount, survival, 0.6)
    assert high > low


def test_accrued_interest_linear() -> None:
    bond = _bullet(80.0, 2)
    accrued = accrued_interest(bond, BASE + timedelta(days=182))
    assert abs(accrued - 80.0 * 182 / 365) < 1e-9


def test_bond_yield_roundtrips_flat_discount() -> None:
    import math

    bond = _bullet(80.0, 4)
    rate = 0.10  # continuously compounded
    discount = _flat_discount(rate)
    dirty = sum(
        (cf.coupon + cf.principal) * discount.discount_factor(cf.date)
        for cf in bond.cashflows
    )
    # Effective annual yield of a flat continuous curve is exp(r) - 1.
    assert abs(bond_yield(bond, dirty, BASE) - (math.exp(rate) - 1.0)) < 1e-6
