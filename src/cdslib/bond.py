from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from scipy.optimize import brentq

from cdslib.curve import ZeroCurve
from cdslib.survival import SurvivalCurve


@dataclass(frozen=True)
class BondCashflow:
    """A dated bond cashflow, in currency units per the bond's notional.

    ``coupon`` is the coupon amount paid at ``date``; ``principal`` is any
    principal repaid at ``date`` (amortization, and the final redemption).
    """

    date: date
    coupon: float
    principal: float = 0.0


@dataclass(frozen=True)
class Bond:
    """A fixed-coupon bond reduced to its cashflow schedule.

    Cashflows run to the *effective maturity* (the offer/put date for bonds
    priced to put, otherwise the legal maturity), where the final ``principal``
    repays the outstanding notional. ``first_accrual_date`` is only used by the
    :meth:`accrued_interest` helper.
    """

    isin: str
    face_value: float
    cashflows: tuple[BondCashflow, ...]
    first_accrual_date: date | None = None

    @property
    def effective_maturity(self) -> date:
        return max(cf.date for cf in self.cashflows)


def _future(cashflows: Iterable[BondCashflow], after: date) -> list[BondCashflow]:
    return sorted((cf for cf in cashflows if cf.date > after), key=lambda cf: cf.date)


def accrued_interest(bond: Bond, valuation_date: date) -> float:
    """Linear (ACT/ACT-in-period) accrued interest as of ``valuation_date``.

    Convenience for tests / fallback; the app normally uses the exchange's
    reported accrued interest.
    """
    coupons = sorted((cf for cf in bond.cashflows if cf.coupon), key=lambda cf: cf.date)
    nxt = next((cf for cf in coupons if cf.date > valuation_date), None)
    if nxt is None:
        return 0.0
    previous = max(
        (cf.date for cf in coupons if cf.date <= valuation_date),
        default=bond.first_accrual_date,
    )
    if previous is None or valuation_date <= previous:
        return 0.0
    period = (nxt.date - previous).days
    if period <= 0:
        return 0.0
    return nxt.coupon * (valuation_date - previous).days / period


def price_bond(
    bond: Bond,
    discount: ZeroCurve,
    survival: SurvivalCurve,
    recovery: float,
) -> float:
    """Reduced-form (intensity model) dirty price of ``bond``.

    ``PV = Σ (cᵢ+prinᵢ)·P(tᵢ)·Q(tᵢ) + R·Nᵢ·P(tᵢ)·(Q(tᵢ₋₁)−Q(tᵢ))`` where ``P``
    is the risk-free discount, ``Q`` the survival probability, ``R`` the recovery
    rate and ``Nᵢ`` the notional outstanding over the period ending at ``tᵢ``.
    Discounting/survival are taken as of ``discount.base_date``.
    """
    base = discount.base_date
    flows = _future(bond.cashflows, base)
    if not flows:
        return 0.0

    outstanding = bond.face_value - sum(
        cf.principal for cf in bond.cashflows if cf.date <= base
    )
    prev_survival = 1.0
    pv = 0.0
    for cf in flows:
        disc = discount.discount_factor(cf.date)
        surv = survival.survival(cf.date)
        pv += (cf.coupon + cf.principal) * disc * surv
        pv += recovery * outstanding * disc * (prev_survival - surv)
        prev_survival = surv
        outstanding -= cf.principal
    return pv


def bond_yield(
    bond: Bond, dirty_price: float, valuation_date: date, basis: float = 365.0
) -> float:
    """Effective annual yield that discounts the bond's cashflows to ``dirty_price``.

    Solves ``dirty = Σ CFᵢ · (1 + y)^(-τᵢ)`` with ``τ`` in ACT/365 years to each
    future cashflow (to the effective maturity). Returns ``nan`` if it cannot be
    bracketed.
    """
    flows = [
        ((cf.date - valuation_date).days / basis, cf.coupon + cf.principal)
        for cf in _future(bond.cashflows, valuation_date)
    ]
    flows = [(t, a) for t, a in flows if a != 0.0 and t > 0]
    if not flows or dirty_price <= 0:
        return float("nan")

    def diff(y: float) -> float:
        return sum(a * math.pow(1.0 + y, -t) for t, a in flows) - dirty_price

    try:
        return float(brentq(diff, -0.99, 10.0, maxiter=200))
    except ValueError:
        return float("nan")
