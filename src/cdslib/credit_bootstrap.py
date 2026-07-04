from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt
from scipy.optimize import least_squares

from cdslib.bond import Bond, price_bond
from cdslib.curve import ZeroCurve
from cdslib.survival import HazardPoint, SurvivalCurve

Vector = npt.NDArray[np.float64]

_BASIS = 365.0
_HAZARD_BOUNDS = (1e-8, 3.0)
_LAMBDA_LOW = 1e-8
_LAMBDA_HIGH = 1e1
_LAMBDA_GRID = 15


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


def bootstrap_credit_curve(
    bonds: Sequence[Bond],
    market_dirty_prices: Sequence[float],
    discount_curve: ZeroCurve,
    recovery: float,
    weights: Sequence[float] | None = None,
) -> CreditCurveResult:
    """Bootstrap an issuer survival (hazard) curve from bond dirty prices.

    Fits a piecewise-constant hazard curve (pillars at each bond's effective
    maturity) so the reduced-form model dirty prices match the market prices,
    weighted by reliability, with a forward-hazard smoothness penalty whose
    strength is chosen at the L-curve knee. Discounting is taken from
    ``discount_curve`` (anchored at the trade date); ``recovery`` is the
    recovery rate (LGD = 1 - recovery).
    """
    if not bonds:
        raise ValueError("At least one bond is required")
    if len(bonds) != len(market_dirty_prices):
        raise ValueError("bonds and market_dirty_prices must have equal length")

    base = discount_curve.base_date
    faces = np.asarray([b.face_value for b in bonds], dtype=np.float64)
    market = np.asarray(market_dirty_prices, dtype=np.float64)

    if weights is None:
        weight_arr = np.ones(len(bonds), dtype=np.float64)
    else:
        weight_arr = np.asarray(weights, dtype=np.float64)
        mean = weight_arr.mean()
        weight_arr = weight_arr / mean if mean > 0 else np.ones(len(bonds))

    eff_days = [float((b.effective_maturity - base).days) for b in bonds]
    if min(eff_days) <= 0:
        raise ValueError("All bonds must mature after the trade date")
    pillar_days = np.asarray(sorted(set(eff_days)), dtype=np.float64)
    pillar_t = pillar_days / _BASIS
    n_pillars = pillar_days.size

    def build_curve(hazards: Vector) -> SurvivalCurve:
        points = [
            HazardPoint(tenor_days=int(pillar_days[k]), hazard=float(hazards[k]))
            for k in range(n_pillars)
        ]
        return SurvivalCurve(base, points)

    def model_prices(hazards: Vector) -> Vector:
        curve = build_curve(hazards)
        return np.asarray(
            [price_bond(b, discount_curve, curve, recovery) for b in bonds],
            dtype=np.float64,
        )

    def forward_hazards(hazards: Vector) -> Vector:
        integrated = np.concatenate([[0.0], hazards * pillar_t])
        times = np.concatenate([[0.0], pillar_t])
        return np.diff(integrated) / np.diff(times)

    def fit_residual(hazards: Vector) -> Vector:
        return weight_arr * (model_prices(hazards) - market) / faces

    def residuals(hazards: Vector, lam: float) -> Vector:
        fit = fit_residual(hazards)
        if n_pillars >= 2:
            smooth = math.sqrt(lam) * np.diff(forward_hazards(hazards))
        else:
            smooth = np.empty(0, dtype=np.float64)
        return np.concatenate([fit, smooth])

    lower = np.full(n_pillars, _HAZARD_BOUNDS[0])
    upper = np.full(n_pillars, _HAZARD_BOUNDS[1])
    x0 = np.full(n_pillars, 0.02, dtype=np.float64)

    def solve(lam: float) -> Vector:
        result = least_squares(
            residuals, x0, args=(lam,), bounds=(lower, upper), method="trf"
        )
        return np.asarray(result.x, dtype=np.float64)

    if n_pillars >= 3:
        lambdas = np.geomspace(_LAMBDA_LOW, _LAMBDA_HIGH, _LAMBDA_GRID)
        solutions = [solve(float(lam)) for lam in lambdas]
        fit_norms = np.asarray(
            [float(np.linalg.norm(fit_residual(h))) for h in solutions]
        )
        rough_norms = np.asarray(
            [float(np.linalg.norm(np.diff(forward_hazards(h)))) for h in solutions]
        )
        corner = _lcurve_corner(fit_norms, rough_norms)
        best_hazards = solutions[corner]
        best_lambda = float(lambdas[corner])
    else:
        best_hazards = solve(0.0)
        best_lambda = 0.0

    curve = build_curve(best_hazards)
    models = model_prices(best_hazards)
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
