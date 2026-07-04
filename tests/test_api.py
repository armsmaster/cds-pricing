from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.backend import moex
from app.backend.db import create_session_factory, get_session
from app.backend.main import app, get_moex

FIXTURES = Path(__file__).parent / "fixtures" / "moex"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeMoex:
    """Fake MOEX client backed by recorded fixtures (no network)."""

    def __init__(self) -> None:
        self._security = moex.parse_security(_load("security.json"))
        self._schedule = moex.parse_bondization(_load("bondization.json"))

    def security(self, isin: str) -> moex.MoexBondRef:
        return self._security

    def bondization(self, isin: str) -> moex.BondSchedule:
        return self._schedule

    def quote(
        self, isin: str, trade_date: date | None = None, board: str = "TQCB"
    ) -> moex.PriceQuote:
        return moex.parse_live_quote(_load("marketdata.json"), trade_date or date.today())

    def search(self, query: str) -> list[dict[str, Any]]:
        return moex.rows(_load("search.json"), "securities")

    def close(self) -> None:
        pass


RUONIA_QUOTES = [
    {"rate": "RUONIA", "tenor": t, "bid": b, "ask": a}
    for t, b, a in [
        ("1m", 14.15, 14.35),
        ("3m", 14.13, 14.38),
        ("6m", 14.32, 14.57),
        ("1y", 14.48, 14.68),
        ("2y", 14.27, 14.47),
        ("3y", 13.29, 14.49),
        ("5y", 14.40, 14.59),
        ("7y", 14.48, 14.68),
    ]
]
TRADE = "2024-06-03"
ISIN = "RU000A10B115"


@pytest.fixture
def client(tmp_path: Any) -> Iterator[TestClient]:
    factory = create_session_factory(f"sqlite:///{tmp_path}/api.db")

    def _session() -> Iterator[Any]:
        with factory() as session:
            yield session

    fake = FakeMoex()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_moex] = lambda: fake
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_issuer_crud(client: TestClient) -> None:
    created = client.post(
        "/api/issuers", json={"name": "RZD", "recovery_rate": 0.4}
    ).json()
    assert created["name"] == "RZD"
    issuer_id = created["id"]

    assert client.get("/api/issuers").json()[0]["name"] == "RZD"

    patched = client.patch(
        f"/api/issuers/{issuer_id}", json={"recovery_rate": 0.35}
    ).json()
    assert patched["recovery_rate"] == 0.35


def test_moex_search(client: TestClient) -> None:
    result = client.get("/api/moex/search", params={"q": ISIN}).json()
    assert result[0]["isin"] == ISIN


def test_add_bond_and_marketdata(client: TestClient) -> None:
    issuer_id = client.post("/api/issuers", json={"name": "RZD"}).json()["id"]
    bond = client.post(f"/api/issuers/{issuer_id}/bonds", json={"isin": ISIN}).json()
    assert bond["isin"] == ISIN
    assert client.get(f"/api/issuers/{issuer_id}/bonds").json()[0]["isin"] == ISIN

    md = client.get(f"/api/bonds/{ISIN}/marketdata", params={"date": TRADE}).json()
    assert md["is_priced"]
    assert md["source"] == "mid"
    assert md["dirty_price"] == pytest.approx(107.36 / 100 * 1000 + 3.38)


def test_credit_curve_end_to_end(client: TestClient) -> None:
    issuer_id = client.post("/api/issuers", json={"name": "RZD", "recovery_rate": 0.4}).json()["id"]
    client.post(f"/api/issuers/{issuer_id}/bonds", json={"isin": ISIN})

    saved = client.post(
        "/api/rate-curves",
        json={"quotes": RUONIA_QUOTES, "trade_date": TRADE, "max_adjustment_bps": 15},
    ).json()
    rate_curve_id = saved["id"]
    assert saved["rate_index"] == "RUONIA"
    assert len(client.get("/api/rate-curves").json()) == 1

    resp = client.post(
        "/api/credit-curve",
        json={"issuer_id": issuer_id, "rate_curve_id": rate_curve_id, "trade_date": TRADE},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["recovery_rate"] == 0.4
    assert data["skipped"] == []
    assert len(data["fits"]) == 1
    assert data["fits"][0]["isin"] == ISIN
    assert data["curve"]  # monthly hazard grid
    assert data["hazard_export"]
    assert data["fits"][0]["model_clean"] > 0
    assert "residual_pct" in data["fits"][0]
    assert "name" in data["fits"][0]
    assert "market_yield" in data["fits"][0]
    assert all(node["hazard"] >= 0 for node in data["curve"])
    assert all("forward" in node for node in data["curve"])


def test_credit_curve_unknown_issuer(client: TestClient) -> None:
    saved = client.post(
        "/api/rate-curves",
        json={"quotes": RUONIA_QUOTES, "trade_date": TRADE, "max_adjustment_bps": 15},
    ).json()
    resp = client.post(
        "/api/credit-curve",
        json={"issuer_id": 999, "rate_curve_id": saved["id"], "trade_date": TRADE},
    )
    assert resp.status_code == 404
