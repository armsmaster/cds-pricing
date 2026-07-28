from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt
from scipy.optimize import least_squares

from cdslib.curve import CurvePoint, ZeroCurve
from cdslib.ois import OISGenerator
from cdslib.tenor import Tenor, TenorUnit

Vector = npt.NDArray[np.float64]
Matrix = npt.NDArray[np.float64]

_BASIS = 365.0
_DEFAULT_MAX_AVG_ADJ_BPS = 15.0
_MIN_SPREAD_BPS = 1.0
_ZERO_BOUNDS = (-0.05, 3.0)
_LAMBDA_LOW = 1e-5
_LAMBDA_HIGH = 1e3
_LAMBDA_GRID = 28


@dataclass(frozen=True)
class OISQuote:
    """A bid/ask OIS par-rate quote, expressed in percentage points.

    ``bid`` / ``ask`` are percentage points (e.g. ``14.35`` means 14.35%).
    """

    rate_index: str
    tenor: str
    bid: float
    ask: float

    @property
    def mid_pct(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def mid(self) -> float:
        """Mid rate as a decimal (0.1435 for 14.35%)."""
        return self.mid_pct / 100.0

    @property
    def bid_rate(self) -> float:
        return self.bid / 100.0

    @property
    def ask_rate(self) -> float:
        return self.ask / 100.0

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) * 100.0


@dataclass(frozen=True)
class InstrumentFit:
    """How a single quote was repriced by the fitted curve."""

    tenor: str
    mid_rate: float
    model_rate: float
    adjustment_bps: float
    within_spread: bool


@dataclass(frozen=True)
class BootstrapResult:
    """Output of the smoothing bootstrap."""

    rate_index: str
    trade_date: date
    spot_date: date
    curve: ZeroCurve
    points: tuple[CurvePoint, ...]
    fits: tuple[InstrumentFit, ...]
    average_adjustment_bps: float
    smoothing_lambda: float

    def as_tuples(self) -> list[tuple[int, float]]:
        """Curve as (tenor in calendar days, zero rate) pairs."""
        return [(p.tenor_days, p.zero_rate) for p in self.points]


def _interp_matrix(query_days: Vector, xp: Vector, node_t_full: Vector) -> Matrix:
    """Linear map ``C`` such that the integrated rate at each query equals ``C @ z``.

    The integrated rate ``I(t) = z(t) * t`` is log-linear in ``t``; each query
    lands in one node interval and depends on the two bounding nodes.
    """
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


class _Problem:
    """Vectorised par-rate / smoothness model with an analytic gradient."""

    def __init__(self, mid: Vector, node_days: Vector, weights: Vector) -> None:
        self._mid = mid
        self._weights = weights
        self._node_t = node_days / _BASIS
        self._xp = np.concatenate([[0.0], node_days])
        self._node_t_full = np.concatenate([[0.0], self._node_t])
        self._tau: list[float] = []
        self._end_days: list[float] = []
        self._seg_starts: list[int] = []
        self._mat_days: list[float] = []

    def add_instrument(
        self, end_days: Sequence[float], tau: Sequence[float], mat_days: float
    ) -> None:
        self._seg_starts.append(len(self._tau))
        self._end_days.extend(end_days)
        self._tau.extend(tau)
        self._mat_days.append(mat_days)

    def finalize(self) -> None:
        self._tau_a: Vector = np.asarray(self._tau, dtype=np.float64)
        self._seg = np.asarray(self._seg_starts, dtype=np.intp)
        end_a = np.asarray(self._end_days, dtype=np.float64)
        mat_a = np.asarray(self._mat_days, dtype=np.float64)
        self._c_end = _interp_matrix(end_a, self._xp, self._node_t_full)
        self._c_mat = _interp_matrix(mat_a, self._xp, self._node_t_full)
        self._hs = self._build_smoothness_operator()

    def _build_smoothness_operator(self) -> Matrix:
        """Linear operator ``Hs`` with forward-rate slope = ``Hs @ z``.

        Penalising the first difference of the forward curve drives it toward a
        flat (piecewise-constant) shape, which removes the overshoot/humps that
        a curvature penalty leaves behind.
        """
        node_count = self._node_t.size
        if node_count < 2:
            return np.zeros((0, node_count), dtype=np.float64)
        dt = np.diff(self._node_t_full)
        forward_map = np.zeros((node_count, node_count), dtype=np.float64)
        forward_map[0, 0] = self._node_t[0] / dt[0]
        for n in range(1, node_count):
            forward_map[n, n] = self._node_t[n] / dt[n]
            forward_map[n, n - 1] = -self._node_t[n - 1] / dt[n]
        first_diff = np.zeros((node_count - 1, node_count), dtype=np.float64)
        for k in range(node_count - 1):
            first_diff[k, k] = -1.0
            first_diff[k, k + 1] = 1.0
        return first_diff @ forward_map

    def _price(self, z: Vector) -> tuple[Vector, Vector, Vector, Vector]:
        disc_end = np.exp(-(self._c_end @ z))
        annuity = np.add.reduceat(self._tau_a * disc_end, self._seg)
        disc_mat = np.exp(-(self._c_mat @ z))
        model = (1.0 - disc_mat) / annuity
        return model, disc_end, disc_mat, annuity

    def model_rates(self, z: Vector) -> Vector:
        return self._price(z)[0]

    def mean_abs_adjustment_bps(self, z: Vector) -> float:
        return float(np.mean(np.abs(self.model_rates(z) - self._mid))) * 1e4

    def roughness(self, z: Vector) -> float:
        slope = self._hs @ z
        return float(math.sqrt(slope @ slope))

    def residuals(self, z: Vector, lam: float) -> Vector:
        """Stacked residuals: reliability-weighted par fit + forward slope."""
        fit = self._weights * (self.model_rates(z) - self._mid)
        smooth = math.sqrt(lam) * (self._hs @ z)
        return np.concatenate([fit, smooth])

    def jacobian(self, z: Vector, lam: float) -> Matrix:
        _, disc_end, disc_mat, annuity = self._price(z)
        term1 = (disc_mat / annuity)[:, None] * self._c_mat
        weighted = (self._tau_a * disc_end)[:, None] * self._c_end
        seg_sum = np.add.reduceat(weighted, self._seg, axis=0)
        term2 = ((1.0 - disc_mat) / annuity**2)[:, None] * seg_sum
        jac_fit = self._weights[:, None] * (term1 + term2)
        jac_smooth = math.sqrt(lam) * self._hs
        return np.vstack([jac_fit, jac_smooth])


def _monthly_nodes(
    spot: date, longest_maturity: date, generator: OISGenerator, rate_index: str
) -> list[date]:
    convention = generator.indices.get(rate_index)
    calendar = generator.calendars.get(convention.currency)
    nodes: list[date] = []
    month = 1
    while True:
        raw = Tenor(month, TenorUnit.MONTH).add_to(spot)
        node = calendar.adjust(raw, convention.business_day_convention)
        nodes.append(node)
        if node >= longest_maturity:
            break
        month += 1
    return nodes


def _solve(problem: _Problem, lam: float, x0: Vector) -> Vector:
    lower = np.full(x0.size, _ZERO_BOUNDS[0])
    upper = np.full(x0.size, _ZERO_BOUNDS[1])
    result = least_squares(
        problem.residuals,
        x0,
        jac=problem.jacobian,
        args=(lam,),
        bounds=(lower, upper),
        method="trf",
    )
    return np.asarray(result.x, dtype=np.float64)


def _lcurve_corner(adjustments: Vector, roughness: Vector) -> int:
    """Index of the L-curve knee via maximum (Menger) curvature.

    In normalised log-log space of adjustment vs roughness, the knee is where
    extra smoothing stops buying roughness reduction -- the least adjustment
    that removes genuine kinks. Menger curvature is robust even when the curve
    has no sharp corner (unlike the distance-to-chord heuristic).
    """
    x = np.log10(adjustments + 1e-9)
    y = np.log10(roughness + 1e-15)
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


def bootstrap(
    quotes: Sequence[OISQuote],
    trade_date: date | None = None,
    generator: OISGenerator | None = None,
    max_avg_adjustment_bps: float = _DEFAULT_MAX_AVG_ADJ_BPS,
    smoothing_pct: float = 0.0,
) -> BootstrapResult:
    """Bootstrap a monthly zero curve from bid/ask OIS quotes.

    The curve is fitted so its model OIS par rates track the quote mids while a
    forward-slope penalty keeps the forward curve flat and free of humps. Each
    quote's fit is weighted by its reliability (inverse bid/ask spread), so the
    adjustment needed for smoothness is absorbed mostly by wide-spread, less
    trustworthy quotes. Smoothing is increased to the largest level whose average
    absolute adjustment (model rate minus mid) stays within ``max_avg_adjustment_bps``.

    When ``smoothing_pct`` is 0 (the default), the smoothing weight is chosen at
    the L-curve knee — the least adjustment that removes genuine kinks. Positive
    values interpolate in log space toward the cap (the smoothest curve the cap
    allows): 100 = maximum smoothness, 50 = halfway. The hard cap always applies.
    """
    if not quotes:
        raise ValueError("At least one quote is required")

    rate_index = quotes[0].rate_index.upper()
    if any(q.rate_index.upper() != rate_index for q in quotes):
        raise ValueError("All quotes must reference the same rate index")

    gen = generator if generator is not None else OISGenerator.load_default()
    swaps = [gen.generate(q.rate_index, q.tenor, trade_date) for q in quotes]

    spot = swaps[0].spot_date
    if any(s.spot_date != spot for s in swaps):
        raise ValueError("Quotes resolved to different spot dates")

    longest_maturity = max(s.maturity_date for s in swaps)
    node_dates = _monthly_nodes(spot, longest_maturity, gen, rate_index)
    node_days = np.asarray([(d - spot).days for d in node_dates], dtype=np.float64)

    mid = np.asarray([q.mid for q in quotes], dtype=np.float64)
    spreads = np.asarray(
        [max(q.spread_bps, _MIN_SPREAD_BPS) for q in quotes], dtype=np.float64
    )
    inverse = 1.0 / spreads
    weights = inverse / inverse.mean()

    problem = _Problem(mid, node_days, weights)
    for swap in swaps:
        end_days = [(p.accrual_end - spot).days for p in swap.fixed_periods]
        tau = [p.year_fraction for p in swap.fixed_periods]
        problem.add_instrument(end_days, tau, (swap.maturity_date - spot).days)
    problem.finalize()

    # Initial guess: interpolate mids across maturity onto the monthly grid.
    mat_days = np.asarray([(s.maturity_date - spot).days for s in swaps], dtype=np.float64)
    order = np.argsort(mat_days)
    x0 = np.interp(node_days, mat_days[order], mid[order]).astype(np.float64)

    # Sweep the smoothing weight; pick the L-curve knee (least adjustment that
    # removes genuine kinks), but never exceed the average-adjustment cap.
    # Reliability weighting concentrates that adjustment on wide-spread quotes.
    lambdas = np.geomspace(_LAMBDA_LOW, _LAMBDA_HIGH, _LAMBDA_GRID)
    solutions = [_solve(problem, float(lam), x0) for lam in lambdas]
    adjustments = np.asarray([problem.mean_abs_adjustment_bps(z) for z in solutions])
    roughness = np.asarray([problem.roughness(z) for z in solutions])

    knee = _lcurve_corner(adjustments, roughness)
    within_cap = np.nonzero(adjustments <= max_avg_adjustment_bps)[0]
    cap_index = int(within_cap[-1]) if within_cap.size else 0

    if smoothing_pct <= 0:
        chosen = min(knee, cap_index)
    else:
        log_knee = math.log(max(lambdas[knee], 1e-14))
        log_cap = math.log(max(lambdas[min(cap_index, len(lambdas) - 1)], 1e-14))
        target = log_knee + (smoothing_pct / 100.0) * (log_cap - log_knee)
        chosen = int(np.searchsorted(np.log(lambdas), target))
        chosen = min(max(chosen, knee), cap_index)
    best_z = solutions[chosen]
    best_lambda = float(lambdas[chosen])

    points = tuple(
        CurvePoint(tenor_days=int(node_days[k]), zero_rate=float(best_z[k]))
        for k in range(best_z.size)
    )
    curve = ZeroCurve(spot, points)

    model_rates = problem.model_rates(best_z)
    fits = tuple(
        InstrumentFit(
            tenor=q.tenor,
            mid_rate=q.mid,
            model_rate=float(model_rates[j]),
            adjustment_bps=float(model_rates[j] - q.mid) * 1e4,
            within_spread=bool(q.bid_rate <= model_rates[j] <= q.ask_rate),
        )
        for j, q in enumerate(quotes)
    )

    return BootstrapResult(
        rate_index=rate_index,
        trade_date=swaps[0].trade_date,
        spot_date=spot,
        curve=curve,
        points=points,
        fits=fits,
        average_adjustment_bps=problem.mean_abs_adjustment_bps(best_z),
        smoothing_lambda=best_lambda,
    )
