from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.backend import moex, repositories
from app.backend.db import create_session_factory
from cdslib import CurvePoint, ZeroCurve


@pytest.fixture
def session(tmp_path: object) -> Iterator[Session]:
    url = f"sqlite:///{tmp_path}/test.db"  # type: ignore[operator]
    factory = create_session_factory(url)
    with factory() as sess:
        yield sess


def _ref(isin: str = "RU000TEST001") -> moex.MoexBondRef:
    return moex.MoexBondRef(
        isin=isin,
        secid=isin,
        shortname="TEST",
        name="Test bond",
        emitent_id=712,
        currency="RUB",
        face_value=1000.0,
        initial_face_value=1000.0,
        issue_date=date(2024, 1, 1),
        maturity_date=date(2030, 1, 1),
        offer_date=date(2028, 1, 1),
        coupon_percent=12.0,
        coupon_frequency=2,
        list_level=2,
        bond_type="fixed",
        bond_subtype="to put",
    )


def _schedule() -> moex.BondSchedule:
    coupons = tuple(
        moex.Coupon(
            date=date(2024 + (k // 2), 1 if k % 2 == 0 else 7, 1),
            value=60.0,
            value_prc=12.0,
            record_date=None,
            start_date=date(2024, 1, 1) if k == 0 else None,
        )
        for k in range(8)
    )
    amort = (
        moex.Amortization(
            date=date(2030, 1, 1), value=1000.0, value_prc=100.0, face_value=1000.0
        ),
    )
    offers = (moex.Offer(date=date(2028, 1, 1), price=100.0, offer_type="put"),)
    return moex.BondSchedule(coupons=coupons, amortizations=amort, offers=offers)


def test_issuer_crud(session: Session) -> None:
    issuer = repositories.create_issuer(session, "RZD", recovery_rate=0.4, moex_emitent_id=712)
    assert issuer.id is not None
    assert repositories.list_issuers(session)[0].name == "RZD"
    repositories.set_recovery_rate(session, issuer.id, 0.35)
    assert repositories.get_issuer(session, issuer.id).recovery_rate == 0.35


def test_add_bond_and_convert(session: Session) -> None:
    issuer = repositories.create_issuer(session, "RZD")
    bond = repositories.add_bond(session, issuer.id, _ref(), _schedule())
    assert bond.isin == "RU000TEST001"
    assert repositories.list_bonds(session, issuer.id)[0].isin == bond.isin

    quant = repositories.to_cdslib_bond(bond)
    # Priced to the 2028 put; redeems par there.
    assert quant.effective_maturity == date(2028, 1, 1)
    assert all(cf.date <= date(2028, 1, 1) for cf in quant.cashflows)
    assert quant.cashflows[-1].principal == 1000.0


def test_cascade_delete(session: Session) -> None:
    issuer = repositories.create_issuer(session, "RZD")
    repositories.add_bond(session, issuer.id, _ref(), _schedule())
    repositories.delete_issuer(session, issuer.id)
    assert repositories.list_issuers(session) == []
    assert repositories.get_bond(session, "RU000TEST001") is None


def test_market_data_upsert(session: Session) -> None:
    issuer = repositories.create_issuer(session, "RZD")
    repositories.add_bond(session, issuer.id, _ref(), _schedule())
    q1 = moex.aggregate_quote(date(2024, 6, 3), 1000.0, 5.0, bid=99.0, ask=101.0)
    repositories.upsert_market_data(session, "RU000TEST001", q1)
    q2 = moex.aggregate_quote(date(2024, 6, 3), 1000.0, 5.0, last=102.0)
    repositories.upsert_market_data(session, "RU000TEST001", q2)
    stored = repositories.get_market_data(session, "RU000TEST001", date(2024, 6, 3))
    assert stored is not None
    assert stored.source == "last"  # updated in place
    assert stored.clean_price == 102.0


def test_rate_curve_roundtrip(session: Session) -> None:
    curve = ZeroCurve(date(2024, 6, 4), [CurvePoint(365, 0.14), CurvePoint(730, 0.135)])
    saved = repositories.save_rate_curve(
        session, "RUONIA", curve, trade_date=date(2024, 6, 3), max_adjustment_bps=15.0
    )
    assert saved.id is not None
    fetched = repositories.get_rate_curve(session, saved.id)
    assert fetched is not None
    rebuilt = repositories.to_zero_curve(fetched)
    assert rebuilt.base_date == date(2024, 6, 4)
    assert abs(rebuilt.zero_rate_days(365) - 0.14) < 1e-9
    # Upsert by (rate_index, trade_date) keeps a single row.
    repositories.save_rate_curve(session, "RUONIA", curve, trade_date=date(2024, 6, 3))
    assert len(repositories.list_rate_curves(session)) == 1
