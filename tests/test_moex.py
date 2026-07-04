from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.backend.moex import (
    MoexClient,
    aggregate_quote,
    build_bond,
    parse_bondization,
    parse_history_quote,
    parse_live_quote,
    parse_security,
    rows,
)

FIXTURES = Path(__file__).parent / "fixtures" / "moex"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_security() -> None:
    ref = parse_security(_load("security.json"))
    assert ref.isin == "RU000A10B115"
    assert ref.secid == "RU000A10B115"
    assert ref.currency == "RUB"
    assert ref.face_value == 1000.0
    assert ref.maturity_date == date(2035, 1, 14)
    assert ref.offer_date == date(2030, 5, 15)
    assert ref.coupon_percent == 17.65
    assert ref.coupon_frequency == 12


def test_parse_bondization() -> None:
    schedule = parse_bondization(_load("bondization.json"))
    assert len(schedule.coupons) > 0
    assert schedule.coupons[0].date == date(2025, 4, 6)
    assert schedule.coupons[0].value == 14.51
    assert schedule.offers[0].date == date(2030, 5, 15)
    assert schedule.amortizations[0].date == date(2035, 1, 14)
    # Post-offer coupons are unknown (value is None).
    assert any(c.value is None for c in schedule.coupons)


def test_build_bond_price_to_put() -> None:
    ref = parse_security(_load("security.json"))
    schedule = parse_bondization(_load("bondization.json"))
    bond = build_bond(ref, schedule, price_to_put=True)

    assert bond.effective_maturity == date(2030, 5, 15)
    assert bond.first_accrual_date == date(2025, 3, 7)
    # No cashflows beyond the offer, and it redeems par there.
    assert all(cf.date <= date(2030, 5, 15) for cf in bond.cashflows)
    last = bond.cashflows[-1]
    assert last.date == date(2030, 5, 15)
    assert last.principal == 1000.0
    # Only known coupons are used, and there are some.
    assert sum(cf.coupon for cf in bond.cashflows) > 0


def test_build_bond_to_maturity() -> None:
    ref = parse_security(_load("security.json"))
    schedule = parse_bondization(_load("bondization.json"))
    bond = build_bond(ref, schedule, price_to_put=False)
    assert bond.effective_maturity == date(2035, 1, 14)
    assert bond.cashflows[-1].date == date(2035, 1, 14)
    assert bond.cashflows[-1].principal == 1000.0


def test_parse_live_quote() -> None:
    quote = parse_live_quote(_load("marketdata.json"), date(2026, 7, 4))
    assert quote.source == "mid"
    assert quote.clean_price == pytest.approx(107.36)
    assert quote.accrued == 3.38
    assert quote.dirty_price == pytest.approx(107.36 / 100 * 1000 + 3.38)
    assert quote.weight == pytest.approx(20.0)
    assert quote.is_priced


def test_parse_history_quote() -> None:
    quote = parse_history_quote(_load("history.json"), date(2025, 6, 3))
    assert quote.source == "waprice"
    assert quote.clean_price == pytest.approx(107.06)
    assert quote.accrued == 13.54
    assert quote.dirty_price == pytest.approx(107.06 / 100 * 1000 + 13.54)
    assert quote.is_priced


def test_aggregate_prefers_mid_then_falls_back() -> None:
    mid = aggregate_quote(date(2024, 1, 1), 1000.0, 5.0, bid=99.0, ask=101.0)
    assert mid.source == "mid"
    assert mid.clean_price == 100.0
    only_wap = aggregate_quote(date(2024, 1, 1), 1000.0, 5.0, waprice=98.0)
    assert only_wap.source == "waprice"
    nothing = aggregate_quote(date(2024, 1, 1), 1000.0, 5.0)
    assert not nothing.is_priced
    assert nothing.weight == 0.0


def test_search_fixture() -> None:
    result = rows(_load("search.json"), "securities")
    assert result
    assert result[0]["isin"] == "RU000A10B115"


@pytest.mark.skipif(os.environ.get("MOEX_LIVE") != "1", reason="requires network")
def test_live_client() -> None:
    with MoexClient() as client:
        ref = client.security("RU000A10B115")
        assert ref.isin == "RU000A10B115"
        schedule = client.bondization("RU000A10B115")
        assert schedule.coupons
