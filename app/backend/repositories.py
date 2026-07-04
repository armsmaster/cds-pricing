from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend import moex
from app.backend.models import Bond, BondCashflow, Issuer, MarketData, RateCurve
from cdslib import Bond as QuantBond
from cdslib import CurvePoint, ZeroCurve

# --- issuers --------------------------------------------------------------


def create_issuer(
    session: Session,
    name: str,
    recovery_rate: float = 0.40,
    moex_emitent_id: int | None = None,
) -> Issuer:
    issuer = Issuer(
        name=name, recovery_rate=recovery_rate, moex_emitent_id=moex_emitent_id
    )
    session.add(issuer)
    session.commit()
    session.refresh(issuer)
    return issuer


def list_issuers(session: Session) -> list[Issuer]:
    return list(session.scalars(select(Issuer).order_by(Issuer.name)))


def get_issuer(session: Session, issuer_id: int) -> Issuer | None:
    return session.get(Issuer, issuer_id)


def set_recovery_rate(session: Session, issuer_id: int, recovery_rate: float) -> Issuer:
    issuer = session.get(Issuer, issuer_id)
    if issuer is None:
        raise KeyError(f"Unknown issuer {issuer_id}")
    issuer.recovery_rate = recovery_rate
    session.commit()
    session.refresh(issuer)
    return issuer


def delete_issuer(session: Session, issuer_id: int) -> None:
    issuer = session.get(Issuer, issuer_id)
    if issuer is not None:
        session.delete(issuer)
        session.commit()


# --- bonds ----------------------------------------------------------------


def add_bond(
    session: Session,
    issuer_id: int,
    ref: moex.MoexBondRef,
    schedule: moex.BondSchedule,
    board: str | None = None,
) -> Bond:
    if ref.face_value is None:
        raise ValueError(f"{ref.isin}: missing face value")

    starts = [c.start_date for c in schedule.coupons if c.start_date is not None]
    first_accrual = min(starts) if starts else ref.issue_date

    bond = Bond(
        isin=ref.isin,
        secid=ref.secid,
        issuer_id=issuer_id,
        shortname=ref.shortname,
        name=ref.name,
        currency=ref.currency,
        face_value=ref.face_value,
        initial_face_value=ref.initial_face_value,
        issue_date=ref.issue_date,
        maturity_date=ref.maturity_date,
        offer_date=ref.offer_date,
        first_accrual_date=first_accrual,
        coupon_percent=ref.coupon_percent,
        coupon_frequency=ref.coupon_frequency,
        list_level=ref.list_level,
        board=board,
        bond_type=ref.bond_type,
        bond_subtype=ref.bond_subtype,
    )
    for coupon in schedule.coupons:
        bond.cashflows.append(
            BondCashflow(
                kind="coupon",
                date=coupon.date,
                value=coupon.value,
                value_prc=coupon.value_prc,
                record_date=coupon.record_date,
                start_date=coupon.start_date,
            )
        )
    for amort in schedule.amortizations:
        bond.cashflows.append(
            BondCashflow(
                kind="amort",
                date=amort.date,
                value=amort.value,
                value_prc=amort.value_prc,
                face_value=amort.face_value,
            )
        )
    for offer in schedule.offers:
        bond.cashflows.append(
            BondCashflow(kind="offer", date=offer.date, value=offer.price)
        )

    session.merge(bond)
    session.commit()
    stored = session.get(Bond, ref.isin)
    assert stored is not None
    return stored


def list_bonds(session: Session, issuer_id: int) -> list[Bond]:
    return list(
        session.scalars(
            select(Bond).where(Bond.issuer_id == issuer_id).order_by(Bond.isin)
        )
    )


def get_bond(session: Session, isin: str) -> Bond | None:
    return session.get(Bond, isin)


def delete_bond(session: Session, isin: str) -> None:
    bond = session.get(Bond, isin)
    if bond is not None:
        session.delete(bond)
        session.commit()


def bond_to_ref(bond: Bond) -> moex.MoexBondRef:
    return moex.MoexBondRef(
        isin=bond.isin,
        secid=bond.secid,
        shortname=bond.shortname,
        name=bond.name,
        emitent_id=None,
        currency=bond.currency,
        face_value=bond.face_value,
        initial_face_value=bond.initial_face_value,
        issue_date=bond.issue_date,
        maturity_date=bond.maturity_date,
        offer_date=bond.offer_date,
        coupon_percent=bond.coupon_percent,
        coupon_frequency=bond.coupon_frequency,
        list_level=bond.list_level,
        bond_type=bond.bond_type,
        bond_subtype=bond.bond_subtype,
    )


def bond_to_schedule(bond: Bond) -> moex.BondSchedule:
    coupons = tuple(
        moex.Coupon(
            date=cf.date,
            value=cf.value,
            value_prc=cf.value_prc,
            record_date=cf.record_date,
            start_date=cf.start_date,
        )
        for cf in bond.cashflows
        if cf.kind == "coupon"
    )
    amortizations = tuple(
        moex.Amortization(
            date=cf.date, value=cf.value, value_prc=cf.value_prc, face_value=cf.face_value
        )
        for cf in bond.cashflows
        if cf.kind == "amort"
    )
    offers = tuple(
        moex.Offer(date=cf.date, price=cf.value, offer_type=None)
        for cf in bond.cashflows
        if cf.kind == "offer"
    )
    return moex.BondSchedule(
        coupons=coupons, amortizations=amortizations, offers=offers
    )


def to_cdslib_bond(bond: Bond, *, price_to_put: bool = True) -> QuantBond:
    return moex.build_bond(
        bond_to_ref(bond), bond_to_schedule(bond), price_to_put=price_to_put
    )


# --- market data ----------------------------------------------------------


def upsert_market_data(
    session: Session, isin: str, quote: moex.PriceQuote
) -> MarketData:
    existing = session.scalar(
        select(MarketData).where(
            MarketData.bond_isin == isin, MarketData.trade_date == quote.trade_date
        )
    )
    record = existing or MarketData(bond_isin=isin, trade_date=quote.trade_date)
    record.source = quote.source
    record.is_priced = quote.is_priced
    record.clean_price = quote.clean_price
    record.dirty_price = quote.dirty_price
    record.accrued = quote.accrued
    record.face_value = quote.face_value
    record.weight = quote.weight
    record.bid = quote.bid
    record.ask = quote.ask
    record.last = quote.last
    record.waprice = quote.waprice
    record.close = quote.close
    record.legal_close = quote.legal_close
    record.market_price = quote.market_price
    if existing is None:
        session.add(record)
    session.commit()
    session.refresh(record)
    return record


def get_market_data(
    session: Session, isin: str, trade_date: object
) -> MarketData | None:
    return session.scalar(
        select(MarketData).where(
            MarketData.bond_isin == isin, MarketData.trade_date == trade_date
        )
    )


def set_price_override(
    session: Session,
    isin: str,
    trade_date: object,
    override_clean: float | None,
    included: bool,
) -> MarketData:
    record = get_market_data(session, isin, trade_date)
    if record is None:
        raise KeyError(f"No market data for {isin} on {trade_date}")
    record.override_clean = override_clean
    record.included = included
    session.commit()
    session.refresh(record)
    return record


# --- rate curves ----------------------------------------------------------


def save_rate_curve(
    session: Session,
    rate_index: str,
    curve: ZeroCurve,
    *,
    zcyc_json: str | None = None,
    quotes_json: str | None = None,
    max_adjustment_bps: float | None = None,
    average_adjustment_bps: float | None = None,
    trade_date: object | None = None,
) -> RateCurve:
    when = trade_date if trade_date is not None else curve.base_date
    points_json = json.dumps([[p.tenor_days, p.zero_rate] for p in curve.points()])
    existing = session.scalar(
        select(RateCurve).where(
            RateCurve.rate_index == rate_index, RateCurve.trade_date == when
        )
    )
    record = existing or RateCurve(rate_index=rate_index, trade_date=when)
    record.spot_date = curve.base_date
    record.points_json = points_json
    record.zcyc_json = zcyc_json
    record.quotes_json = quotes_json
    record.max_adjustment_bps = max_adjustment_bps
    record.average_adjustment_bps = average_adjustment_bps
    if existing is None:
        session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_rate_curves(session: Session) -> list[RateCurve]:
    return list(
        session.scalars(
            select(RateCurve).order_by(RateCurve.rate_index, RateCurve.trade_date)
        )
    )


def get_rate_curve(session: Session, curve_id: int) -> RateCurve | None:
    return session.get(RateCurve, curve_id)


def delete_rate_curve(session: Session, curve_id: int) -> None:
    record = session.get(RateCurve, curve_id)
    if record is not None:
        session.delete(record)
        session.commit()


def to_zero_curve(record: RateCurve) -> ZeroCurve:
    base = record.spot_date or record.trade_date
    points = [
        CurvePoint(tenor_days=int(d), zero_rate=float(r))
        for d, r in json.loads(record.points_json)
    ]
    return ZeroCurve(base, points)
