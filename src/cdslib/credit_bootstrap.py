from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt
from scipy.optimize import least_squares

from cdslib.bond import Bond
from cdslib.curve import ZeroCurve
from cdslib.survival import HazardPoint, SurvivalCurve
from cdslib.tenor import Tenor, TenorUnit

Vector = npt.NDArray[np.float64]
Matrix = npt.NDArray[np.float64]

_BASIS = 365.0
_HAZARD_BOUNDS = (1e-8, 3.0)
_LAMBDA_LOW = 1e-7
_LAMBDA_HIGH = 1e2
_LAMBDA_GRID = 22


@dataclass(frozen=True)
class BondPriceFit:
    """How a single bond was repriced by the fitted credit curve."""

    isin: str
    effective_maturity: date
    years: float
    market_dirty: float
    model_dirty: float
    residual: float
    weight: float


@dataclass(frozen=True)
class CreditCurveResult:
    base_date: date
    recovery_rate: float
    curve: SurvivalCurve
    fits: tuple[BondPriceFit, ...]
    smoothing_lambda: float


def _interp_matrix(query_days: Vector, xp: Vector, node_t_full: Vector) -> Matrix:
    """Linear map ``C`` so integrated hazard at each query equals ``C @ h``."""
    node_count = node_t_full.size - 1
    matrix = np.zeros((query_days.size, node_count), dtype=np.float64)
    for row, query in enumerate(query_days):
        upper = int(np.searchsorted(xp, query, side="right"))
        lower = min(max(upper - 1, 0), xp.size - 2)
        span = xp[lower + 1] - xp[lower]
        frac = 0.0 if span == 0 else (query - xp[lower]) / span
        for node, weight in ((lower, 1.0 - frac), (lower + 1, frac)):
            if node >= 1:
                matrix[row, node - 1] += weight * node_t_full[node]
    return matrix


def _lcurve_corner(fit_norms: Vector, rough_norms: Vector) -> int:
    """Index of the L-curve knee via maximum (Menger) curvature in log-log space."""
    x = np.log10(fit_norms + 1e-15)
    y = np.log10(rough_norms + 1e-15)
    x_span, y_span = x.max() - x.min(), y.max() - y.min()
    if x_span <= 0 or y_span <= 0:
        return 0
    x = (x - x.min()) / x_span
    y = (y - y.min()) / y_span
    best_index, best_curvature = 0, -1.0
    for i in range(1, x.size - 1):
        a = math.hypot(x[i] - x[i - 1], y[i] - y[i - 1])
        b = math.hypot(x[i + 1] - x[i], y[i + 1] - y[i])
        c = math.hypot(x[i + 1] - x[i - 1], y[i + 1] - y[i - 1])
        if a * b * c == 0:
            continue
        area = abs(
            (x[i] - x[i - 1]) * (y[i + 1] - y[i - 1])
            - (x[i + 1] - x[i - 1]) * (y[i] - y[i - 1])
        ) / 2.0
        curvature = 4.0 * area / (a * b * c)
        if curvature > best_curvature:
            best_index, best_curvature = i, curvature
    return best_index


def _monthly_node_days(base: date, last_day: int) -> list[int]:
    days: list[int] = []
    month = 1
    while True:
        node = (Tenor(month, TenorUnit.MONTH).add_to(base) - base).days
        days.append(node)
        if node >= last_day:
            break
        month += 1
    return days


class _Problem:
    """Vectorised reduced-form bond pricing on a monthly hazard grid."""

    def __init__(
        self,
        base: date,
        bonds: Sequence[Bond],
        discount: ZeroCurve,
        recovery: float,
        market: Vector,
        weights: Vector,
    ) -> None:
        self._market = market
        self._weights = weights
        self._recovery = recovery
        faces: list[float] = []
        amounts: list[float] = []
        discs: list[float] = []
        outstanding_row: list[float] = []
        seg_starts: list[int] = []
        query_days: list[float] = []

        for bond in bonds:
            flows = sorted(
                (cf for cf in bond.cashflows if cf.date > base), key=lambda cf: cf.date
            )
            seg_starts.append(len(query_days))
            faces.append(bond.face_value)
            outstanding = bond.face_value - sum(
                cf.principal for cf in bond.cashflows if cf.date <= base
            )
            for cf in flows:
                query_days.append(float((cf.date - base).days))
                amounts.append(cf.coupon + cf.principal)
                discs.append(discount.discount_factor(cf.date))
                outstanding_row.append(outstanding)
                outstanding -= cf.principal

        self._faces = np.asarray(faces, dtype=np.float64)
        self._amount = np.asarray(amounts, dtype=np.float64)
        self._disc = np.asarray(discs, dtype=np.float64)
        self._outstanding = np.asarray(outstanding_row, dtype=np.float64)
        self._seg = np.asarray(seg_starts, dtype=np.intp)

        last_day = int(max(query_days))
        self._node_days = np.asarray(_monthly_node_days(base, last_day), dtype=np.float64)
        self._node_t = self._node_days / _BASIS
        xp = np.concatenate([[0.0], self._node_days])
        node_t_full = np.concatenate([[0.0], self._node_t])
        self._c_cf = _interp_matrix(np.asarray(query_days), xp, node_t_full)
        self.n_nodes = self._node_days.size

    def model_prices(self, hazards: Vector) -> Vector:
        survival = np.exp(-(self._c_cf @ hazards))
        prev = np.concatenate([[1.0], survival[:-1]])
        prev[self._seg] = 1.0
        contrib = self._amount * self._disc * survival + (
            self._recovery * self._outstanding * self._disc * (prev - survival)
        )
        return np.add.reduceat(contrib, self._seg)

    def forward_hazards(self, hazards: Vector) -> Vector:
        integrated = np.concatenate([[0.0], hazards * self._node_t])
        times = np.concatenate([[0.0], self._node_t])
        return np.diff(integrated) / np.diff(times)

    def fit_residual(self, hazards: Vector) -> Vector:
        return self._weights * (self.model_prices(hazards) - self._market) / self._faces

    def residuals(self, hazards: Vector, lam: float) -> Vector:
        fit = self.fit_residual(hazards)
        if self.n_nodes >= 2:
            smooth = math.sqrt(lam) * np.diff(self.forward_hazards(hazards))
        else:
            smooth = np.empty(0, dtype=np.float64)
        return np.concatenate([fit, smooth])


def bootstrap_credit_curve(
    bonds: Sequence[Bond],
    market_dirty_prices: Sequence[float],
    discount_curve: ZeroCurve,
    recovery: float,
    weights: Sequence[float] | None = None,
) -> CreditCurveResult:
    """Bootstrap a smooth issuer survival (hazard) curve from bond dirty prices.

    Mirrors the OIS approach: hazards live on a monthly grid, the bond dirty
    prices are matched (reliability-weighted), and a forward-hazard *slope*
    penalty keeps the instantaneous hazard smooth and hump-free. The smoothing
    strength is chosen at the L-curve knee. Discounting uses ``discount_curve``
    (anchored at the trade date); ``recovery`` is the recovery rate (LGD = 1 - R).
    """
    if not bonds:
        raise ValueError("At least one bond is required")
    if len(bonds) != len(market_dirty_prices):
        raise ValueError("bonds and market_dirty_prices must have equal length")

    base = discount_curve.base_date
    if min((b.effective_maturity - base).days for b in bonds) <= 0:
        raise ValueError("All bonds must mature after the trade date")

    market = np.asarray(market_dirty_prices, dtype=np.float64)
    if weights is None:
        weight_arr = np.ones(len(bonds), dtype=np.float64)
    else:
        weight_arr = np.asarray(weights, dtype=np.float64)
        mean = weight_arr.mean()
        weight_arr = weight_arr / mean if mean > 0 else np.ones(len(bonds))

    problem = _Problem(base, bonds, discount_curve, recovery, market, weight_arr)
    n = problem.n_nodes
    lower = np.full(n, _HAZARD_BOUNDS[0])
    upper = np.full(n, _HAZARD_BOUNDS[1])
    x0 = np.full(n, 0.02, dtype=np.float64)

    def solve(lam: float) -> Vector:
        result = least_squares(
            problem.residuals, x0, args=(lam,), bounds=(lower, upper), method="trf"
        )
        return np.asarray(result.x, dtype=np.float64)

    if n >= 3:
        lambdas = np.geomspace(_LAMBDA_LOW, _LAMBDA_HIGH, _LAMBDA_GRID)
        solutions = [solve(float(lam)) for lam in lambdas]
        fit_norms = np.asarray([float(np.linalg.norm(problem.fit_residual(h))) for h in solutions])
        rough_norms = np.asarray(
            [float(np.linalg.norm(np.diff(problem.forward_hazards(h)))) for h in solutions]
        )
        corner = _lcurve_corner(fit_norms, rough_norms)
        best_hazards = solutions[corner]
        best_lambda = float(lambdas[corner])
    else:
        best_hazards = solve(0.0)
        best_lambda = 0.0

    points = [
        HazardPoint(tenor_days=int(problem._node_days[k]), hazard=float(best_hazards[k]))
        for k in range(n)
    ]
    curve = SurvivalCurve(base, points)

    models = problem.model_prices(best_hazards)
    fits = tuple(
        BondPriceFit(
            isin=bond.isin,
            effective_maturity=bond.effective_maturity,
            years=round((bond.effective_maturity - base).days / _BASIS, 4),
            market_dirty=float(market[j]),
            model_dirty=float(models[j]),
            residual=float(models[j] - market[j]),
            weight=float(weight_arr[j]),
        )
        for j, bond in enumerate(bonds)
    )

    return CreditCurveResult(
        base_date=base,
        recovery_rate=recovery,
        curve=curve,
        fits=fits,
        smoothing_lambda=best_lambda,
    )
