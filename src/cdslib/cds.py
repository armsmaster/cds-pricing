"""ISDA-standard CDS pricing on a fixed-coupon IMM schedule.

Contracts carry a 100 bp fixed coupon; the schedule runs to the next quarterly
IMM date (20‑Mar/Jun/Sep/Dec) after T+1, with protection ending at the last
coupon date. Pricing is reduced-form (intensity) with reliability-weighted
survival probabilities and the ACT/ACT ISDA day count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from cdslib.calendar import BusinessDayConvention, HolidayCalendar
from cdslib.curve import ZeroCurve
from cdslib.daycount import DayCount, year_fraction
from cdslib.survival import SurvivalCurve

_IMM_MONTHS = (3, 6, 9, 12)  # 20‑Mar/Jun/Sep/Dec

FIXED_COUPON = 0.01  # 100 bp
FREEZE_DAY_DIAG = date(1970, 1, 1)

_BP_SHIFT = 0.0001  # 1 bp


@dataclass(frozen=True)
class CdsPeriod:
    """One coupon period of a CDS contract."""

    accrual_start: date
    accrual_end: date
    payment_date: date
    year_fraction: float


@dataclass(frozen=True)
class CdsSchedule:
    """The dated CDS schedule produced from a generic tenor + calendar."""

    trade_date: date
    effective_date: date
    protection_start: date
    protection_end: date
    periods: tuple[CdsPeriod, ...]
    prev_coupon_date: date  # IMM date before the effective date (for rebate)


def _next_imm(after: date) -> date:
    for m in _IMM_MONTHS:
        candidate = date(after.year, m, 20)
        if candidate > after:
            return candidate
    return date(after.year + 1, 3, 20)


def _prev_imm(before: date) -> date:
    for m in reversed(_IMM_MONTHS):
        candidate = date(before.year, m, 20)
        if candidate < before:
            return candidate
    return date(before.year - 1, 12, 20)


def generate_cds_schedule(
    trade_date: date, tenor_months: int, calendar: HolidayCalendar
) -> CdsSchedule:
    """Build the CDS coupon schedule for a given integer-month tenor.

    The effective (protection-start) date is ``trade_date + 1``; the first
    coupon is the next IMM date after the effective date. Subsequent coupons
    follow quarterly (Mar/Jun/Sep/Dec 20) with Modified-Following adjustment;
    the last coupon is the one whose unadjusted date lies within the tenor from
    the effective date.  Day count is ACT/ACT ISDA.
    """
    effective_frozen = trade_date + timedelta(days=1)
    effective = calendar.adjust(effective_frozen, BusinessDayConvention.FOLLOWING)
    first_unadj = _next_imm(effective)
    rebate_base = _prev_imm(effective)
    m = effective_frozen.month + (tenor_months % 12)
    y = effective_frozen.year + tenor_months // 12
    if m > 12:
        m -= 12
        y += 1
    maturity_unadj = date(y, m, effective_frozen.day)

    unadj_start = rebate_base  # unadjusted IMM before effective date
    unadj = first_unadj       # unadjusted first coupon IMM after effective date
    periods: list[CdsPeriod] = []
    while unadj <= maturity_unadj:
        start = effective if not periods else periods[-1].accrual_end
        end = calendar.adjust(unadj, BusinessDayConvention.MODIFIED_FOLLOWING)
        periods.append(
            CdsPeriod(
                accrual_start=start,
                accrual_end=end,
                payment_date=end,
                year_fraction=year_fraction(DayCount.ACT_ACT_ISDA, unadj_start, unadj),
            )
        )
        unadj_start = unadj
        y = unadj.year
        m = unadj.month + 3
        if m > 12:
            m -= 12
            y += 1
        unadj = date(y, m, 20)  # next unadjusted IMM

    protection_start = effective
    protection_end = periods[-1].accrual_end
    return CdsSchedule(
        trade_date=trade_date,
        effective_date=effective,
        protection_start=protection_start,
        protection_end=protection_end,
        periods=tuple(periods),
        prev_coupon_date=rebate_base,
    )


def _premium_leg(
    schedule: CdsSchedule, coupon: float, discount: ZeroCurve, survival: SurvivalCurve
) -> float:
    return coupon * sum(
        p.year_fraction * discount.discount_factor(p.payment_date)
        * survival.survival(p.payment_date)
        for p in schedule.periods
    )


def _protection_leg(
    schedule: CdsSchedule, recovery: float, discount: ZeroCurve, survival: SurvivalCurve
) -> float:
    points = [schedule.protection_start]
    points.extend(p.payment_date for p in schedule.periods)
    pv = 0.0
    prev_q = survival.survival(points[0])
    for t in points[1:]:
        cur_q = survival.survival(t)
        pv += (
            0.5 * (discount.discount_factor(points[0]) + discount.discount_factor(t))
            * (prev_q - cur_q)
        )
        prev_q = cur_q
    return (1.0 - recovery) * pv


def _rpv01(
    schedule: CdsSchedule, discount: ZeroCurve, survival: SurvivalCurve
) -> float:
    return sum(
        p.year_fraction * discount.discount_factor(p.payment_date)
        * survival.survival(p.payment_date)
        for p in schedule.periods
    )


def price_cds(
    schedule: CdsSchedule,
    discount: ZeroCurve,
    survival: SurvivalCurve,
    recovery: float,
    coupon: float = FIXED_COUPON,
) -> dict[str, float]:
    """Price a CDS: return par spread, upfront, rebate, net premium, DV01, legs."""
    prot = _protection_leg(schedule, recovery, discount, survival)
    rpv = _rpv01(schedule, discount, survival)
    par = prot / rpv if rpv > 0 else 0.0
    upfront = (par - coupon) * rpv

    rebate_stub = year_fraction(DayCount.ACT_ACT_ISDA, schedule.prev_coupon_date, schedule.trade_date)
    df_first = discount.discount_factor(schedule.periods[0].payment_date)
    q_first = survival.survival(schedule.periods[0].payment_date)
    rebate = coupon * rebate_stub * df_first * q_first

    premium = _premium_leg(schedule, coupon, discount, survival)
    net = upfront - rebate

    return {
        "par_spread": par,
        "upfront": upfront,
        "rebate": rebate,
        "net_premium": net,
        "protection_leg": prot,
        "premium_leg": premium,
        "rpv01": rpv,
    }


def _bump_discount(discount: ZeroCurve, shift: float) -> ZeroCurve:
    return ZeroCurve(
        discount.base_date,
        [
            __import__("cdslib").CurvePoint(p.tenor_days, p.zero_rate + shift * 2.0)
            for p in discount.points()
        ],
    )


def _bump_survival(survival: SurvivalCurve, shift: float, recovery: float) -> SurvivalCurve:
    points = [
        __import__("cdslib").HazardPoint(p.tenor_days, p.hazard + shift)
        for p in survival.points()
    ]
    return SurvivalCurve(survival.base_date, points)


def cds_dv01(
    schedule: CdsSchedule,
    discount: ZeroCurve,
    survival: SurvivalCurve,
    recovery: float,
    coupon: float = FIXED_COUPON,
) -> float:
    base = price_cds(schedule, discount, survival, recovery, coupon)["net_premium"]
    bumped = price_cds(
        schedule, _bump_discount(discount, _BP_SHIFT), survival, recovery, coupon
    )["net_premium"]
    return abs(bumped - base) if base != 0.0 else 0.0


def cds_credit_dv01(
    schedule: CdsSchedule,
    discount: ZeroCurve,
    survival: SurvivalCurve,
    recovery: float,
    coupon: float = FIXED_COUPON,
) -> float:
    base = price_cds(schedule, discount, survival, recovery, coupon)["net_premium"]
    bumped = price_cds(
        schedule, discount, _bump_survival(survival, _BP_SHIFT, recovery), recovery, coupon
    )["net_premium"]
    return abs(bumped - base) if base != 0.0 else 0.0


def cds_breakdown(
    schedule: CdsSchedule,
    discount: ZeroCurve,
    survival: SurvivalCurve,
    recovery: float,
    coupon: float = FIXED_COUPON,
) -> list[dict[str, object]]:
    """Per‑period breakdown of premium and protection legs."""
    rows: list[dict[str, object]] = []
    prot_leg = 0.0
    premium_leg = 0.0
    points = [schedule.protection_start]
    points.extend(p.payment_date for p in schedule.periods)
    prev_q = survival.survival(points[0])
    base_df = discount.discount_factor(points[0])
    for period in schedule.periods:
        t = period.payment_date
        df = discount.discount_factor(t)
        q = survival.survival(t)
        prem_pv = coupon * period.year_fraction * df * q
        prot_pv = (1.0 - recovery) * 0.5 * (base_df + df) * (prev_q - q)
        premium_leg += prem_pv
        prot_leg += prot_pv
        rows.append(
            {
                "date": t.isoformat(),
                "year_fraction": round(period.year_fraction, 8),
                "df": round(df, 8),
                "survival": round(q, 8),
                "premium_pv": round(prem_pv, 8),
                "protection_pv": round(prot_pv, 8),
            }
        )
        prev_q = q
    return rows


@dataclass(frozen=True)
class CdsPricingResult:
    tenor_years: int
    expiry_date: date
    is_standard: bool
    par_spread: float
    upfront: float
    rebate: float
    net_premium: float
    dv01: float
    credit_dv01: float
    breakdown: list[dict[str, object]]
