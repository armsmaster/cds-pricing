from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.backend.db import Base

# Alias so a column literally named ``date`` does not shadow the ``date`` type.
OptDate = date | None


def _now() -> datetime:
    return datetime.now(UTC)


class Issuer(Base):
    __tablename__ = "issuers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    moex_emitent_id: Mapped[int | None] = mapped_column(default=None)
    recovery_rate: Mapped[float] = mapped_column(default=0.40)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    bonds: Mapped[list[Bond]] = relationship(
        back_populates="issuer", cascade="all, delete-orphan"
    )


class Bond(Base):
    __tablename__ = "bonds"

    isin: Mapped[str] = mapped_column(primary_key=True)
    secid: Mapped[str] = mapped_column(default="")
    issuer_id: Mapped[int] = mapped_column(
        ForeignKey("issuers.id", ondelete="CASCADE")
    )
    shortname: Mapped[str | None] = mapped_column(default=None)
    name: Mapped[str | None] = mapped_column(default=None)
    currency: Mapped[str | None] = mapped_column(default=None)
    face_value: Mapped[float] = mapped_column(default=0.0)
    initial_face_value: Mapped[float | None] = mapped_column(default=None)
    issue_date: Mapped[OptDate] = mapped_column(default=None)
    maturity_date: Mapped[OptDate] = mapped_column(default=None)
    offer_date: Mapped[OptDate] = mapped_column(default=None)
    first_accrual_date: Mapped[OptDate] = mapped_column(default=None)
    coupon_percent: Mapped[float | None] = mapped_column(default=None)
    coupon_frequency: Mapped[int | None] = mapped_column(default=None)
    list_level: Mapped[int | None] = mapped_column(default=None)
    board: Mapped[str | None] = mapped_column(default=None)
    bond_type: Mapped[str | None] = mapped_column(default=None)
    bond_subtype: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    issuer: Mapped[Issuer] = relationship(back_populates="bonds")
    cashflows: Mapped[list[BondCashflow]] = relationship(
        back_populates="bond", cascade="all, delete-orphan"
    )


class BondCashflow(Base):
    __tablename__ = "bond_cashflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_isin: Mapped[str] = mapped_column(
        ForeignKey("bonds.isin", ondelete="CASCADE")
    )
    kind: Mapped[str]  # "coupon" | "amort" | "offer"
    date: Mapped[OptDate] = mapped_column(default=None)
    value: Mapped[float | None] = mapped_column(default=None)
    value_prc: Mapped[float | None] = mapped_column(default=None)
    record_date: Mapped[OptDate] = mapped_column(default=None)
    start_date: Mapped[OptDate] = mapped_column(default=None)
    face_value: Mapped[float | None] = mapped_column(default=None)

    bond: Mapped[Bond] = relationship(back_populates="cashflows")


class MarketData(Base):
    __tablename__ = "market_data"
    __table_args__ = (UniqueConstraint("bond_isin", "trade_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_isin: Mapped[str] = mapped_column(
        ForeignKey("bonds.isin", ondelete="CASCADE")
    )
    trade_date: Mapped[date]
    source: Mapped[str] = mapped_column(default="none")
    is_priced: Mapped[bool] = mapped_column(default=False)
    clean_price: Mapped[float | None] = mapped_column(default=None)
    dirty_price: Mapped[float | None] = mapped_column(default=None)
    accrued: Mapped[float | None] = mapped_column(default=None)
    face_value: Mapped[float | None] = mapped_column(default=None)
    weight: Mapped[float] = mapped_column(default=0.0)
    bid: Mapped[float | None] = mapped_column(default=None)
    ask: Mapped[float | None] = mapped_column(default=None)
    last: Mapped[float | None] = mapped_column(default=None)
    waprice: Mapped[float | None] = mapped_column(default=None)
    close: Mapped[float | None] = mapped_column(default=None)
    legal_close: Mapped[float | None] = mapped_column(default=None)
    market_price: Mapped[float | None] = mapped_column(default=None)
    fetched_at: Mapped[datetime] = mapped_column(default=_now)


class RateCurve(Base):
    __tablename__ = "rate_curves"
    __table_args__ = (UniqueConstraint("rate_index", "trade_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rate_index: Mapped[str]
    trade_date: Mapped[date]
    spot_date: Mapped[OptDate] = mapped_column(default=None)
    points_json: Mapped[str] = mapped_column(default="[]")
    zcyc_json: Mapped[str | None] = mapped_column(default=None)
    quotes_json: Mapped[str | None] = mapped_column(default=None)
    max_adjustment_bps: Mapped[float | None] = mapped_column(default=None)
    average_adjustment_bps: Mapped[float | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)
