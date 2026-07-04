"""MOEX ISS client and parsers for bond reference, schedule and market data.

Fetches from the public ISS API (https://iss.moex.com/iss) and converts the
columnar JSON into typed structures, including a builder that turns a bond's
reference data + coupon schedule into a :class:`cdslib.Bond` priced to its
offer/put date.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from types import TracebackType
from typing import Any

import httpx

from cdslib import Bond, BondCashflow

DEFAULT_BASE_URL = "https://iss.moex.com/iss"
DEFAULT_BOARD = "TQCB"

# Alias so dataclass fields named ``date`` do not shadow the ``date`` type.
OptDate = date | None


# --- low-level helpers ----------------------------------------------------


def rows(payload: dict[str, Any], block: str) -> list[dict[str, Any]]:
    """Turn an ISS columnar block (``columns`` + ``data``) into dict rows."""
    section = payload.get(block)
    if not section or "columns" not in section:
        return []
    columns = section["columns"]
    return [dict(zip(columns, row, strict=True)) for row in section["data"]]


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def _currency(face_unit: Any) -> str | None:
    code = _str(face_unit)
    if code is None:
        return None
    return "RUB" if code.upper() == "SUR" else code.upper()


# --- typed structures -----------------------------------------------------


@dataclass(frozen=True)
class MoexBondRef:
    isin: str
    secid: str
    shortname: str | None
    name: str | None
    emitent_id: int | None
    currency: str | None
    face_value: float | None
    initial_face_value: float | None
    issue_date: date | None
    maturity_date: date | None
    offer_date: date | None
    coupon_percent: float | None
    coupon_frequency: int | None
    list_level: int | None
    bond_type: str | None
    bond_subtype: str | None


@dataclass(frozen=True)
class Coupon:
    date: OptDate
    value: float | None
    value_prc: float | None
    record_date: OptDate
    start_date: OptDate


@dataclass(frozen=True)
class Amortization:
    date: OptDate
    value: float | None
    value_prc: float | None
    face_value: float | None


@dataclass(frozen=True)
class Offer:
    date: OptDate
    price: float | None
    offer_type: str | None


@dataclass(frozen=True)
class BondSchedule:
    coupons: tuple[Coupon, ...]
    amortizations: tuple[Amortization, ...]
    offers: tuple[Offer, ...]


@dataclass(frozen=True)
class PriceQuote:
    """Aggregated market price for a bond on a trade date.

    Prices are clean, in percent of face; ``dirty_price`` is in currency.
    """

    trade_date: date
    source: str
    is_priced: bool
    clean_price: float | None
    dirty_price: float | None
    accrued: float | None
    face_value: float | None
    weight: float
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    waprice: float | None = None
    close: float | None = None
    legal_close: float | None = None
    market_price: float | None = None


# --- parsers --------------------------------------------------------------


def parse_security(payload: dict[str, Any]) -> MoexBondRef:
    desc = {r["name"]: r["value"] for r in rows(payload, "description")}
    return MoexBondRef(
        isin=_str(desc.get("ISIN")) or "",
        secid=_str(desc.get("SECID")) or "",
        shortname=_str(desc.get("SHORTNAME")),
        name=_str(desc.get("NAME")),
        emitent_id=_int(desc.get("EMITTER_ID")),
        currency=_currency(desc.get("FACEUNIT")),
        face_value=_num(desc.get("FACEVALUE")),
        initial_face_value=_num(desc.get("INITIALFACEVALUE")),
        issue_date=_date(desc.get("ISSUEDATE")),
        maturity_date=_date(desc.get("MATDATE")),
        offer_date=_date(desc.get("BUYBACKDATE")),
        coupon_percent=_num(desc.get("COUPONPERCENT")),
        coupon_frequency=_int(desc.get("COUPONFREQUENCY")),
        list_level=_int(desc.get("LISTLEVEL")),
        bond_type=_str(desc.get("BOND_TYPE")),
        bond_subtype=_str(desc.get("BOND_SUBTYPE")),
    )


def parse_bondization(payload: dict[str, Any]) -> BondSchedule:
    coupons = tuple(
        Coupon(
            date=_date(r.get("coupondate")),
            value=_num(r.get("value")),
            value_prc=_num(r.get("valueprc")),
            record_date=_date(r.get("recorddate")),
            start_date=_date(r.get("startdate")),
        )
        for r in rows(payload, "coupons")
    )
    amortizations = tuple(
        Amortization(
            date=_date(r.get("amortdate")),
            value=_num(r.get("value")),
            value_prc=_num(r.get("valueprc")),
            face_value=_num(r.get("facevalue")),
        )
        for r in rows(payload, "amortizations")
    )
    offers = tuple(
        Offer(
            date=_date(r.get("offerdate")),
            price=_num(r.get("price")),
            offer_type=_str(r.get("offertype")),
        )
        for r in rows(payload, "offers")
    )
    return BondSchedule(coupons=coupons, amortizations=amortizations, offers=offers)


def _pick(board_rows: Sequence[dict[str, Any]], board: str) -> dict[str, Any] | None:
    for row in board_rows:
        if row.get("BOARDID") == board:
            return row
    return board_rows[0] if board_rows else None


def aggregate_quote(
    trade_date: date,
    face_value: float | None,
    accrued: float | None,
    *,
    bid: float | None = None,
    ask: float | None = None,
    last: float | None = None,
    waprice: float | None = None,
    close: float | None = None,
    legal_close: float | None = None,
    market_price: float | None = None,
) -> PriceQuote:
    """Pick a representative clean price from whatever signals are available.

    Illiquid bonds rarely quote everything, so this prefers a two-sided
    bid/ask mid, then last, then WAP, then official close prices, and weights
    the observation by how trustworthy it is (tight two-sided quotes highest).
    """
    clean: float | None = None
    source = "none"
    weight = 0.0
    if bid is not None and ask is not None:
        clean, source = 0.5 * (bid + ask), "mid"
        weight = 1.0 / max(ask - bid, 0.05) if ask >= bid else 5.0
    elif last is not None:
        clean, source, weight = last, "last", 8.0
    elif waprice is not None:
        clean, source, weight = waprice, "waprice", 5.0
    elif close is not None:
        clean, source, weight = close, "close", 5.0
    elif legal_close is not None:
        clean, source, weight = legal_close, "legal_close", 5.0
    elif market_price is not None:
        clean, source, weight = market_price, "market_price", 4.0

    dirty: float | None = None
    if clean is not None and face_value is not None:
        dirty = clean / 100.0 * face_value + (accrued or 0.0)

    return PriceQuote(
        trade_date=trade_date,
        source=source,
        is_priced=dirty is not None,
        clean_price=clean,
        dirty_price=dirty,
        accrued=accrued,
        face_value=face_value,
        weight=weight,
        bid=bid,
        ask=ask,
        last=last,
        waprice=waprice,
        close=close,
        legal_close=legal_close,
        market_price=market_price,
    )


def parse_live_quote(
    payload: dict[str, Any], trade_date: date, board: str = DEFAULT_BOARD
) -> PriceQuote:
    security = _pick(rows(payload, "securities"), board) or {}
    market = _pick(rows(payload, "marketdata"), board) or {}
    return aggregate_quote(
        trade_date,
        _num(security.get("FACEVALUE")),
        _num(security.get("ACCRUEDINT")),
        bid=_num(market.get("BID")),
        ask=_num(market.get("OFFER")),
        last=_num(market.get("LAST")),
        waprice=_num(market.get("WAPRICE")),
        close=_num(market.get("CLOSEPRICE")),
        legal_close=_num(market.get("LCLOSEPRICE")),
        market_price=_num(market.get("MARKETPRICE2")),
    )


def parse_history_quote(
    payload: dict[str, Any], trade_date: date, board: str = DEFAULT_BOARD
) -> PriceQuote:
    target = trade_date.isoformat()
    history = [
        r
        for r in rows(payload, "history")
        if r.get("BOARDID") == board and str(r.get("TRADEDATE")) == target
    ]
    row = history[0] if history else {}
    return aggregate_quote(
        trade_date,
        _num(row.get("FACEVALUE")),
        _num(row.get("ACCINT")),
        waprice=_num(row.get("WAPRICE")),
        close=_num(row.get("CLOSE")),
        legal_close=_num(row.get("LEGALCLOSEPRICE")),
        market_price=_num(row.get("MARKETPRICE2")),
    )


# --- bond builder ---------------------------------------------------------


def build_bond(
    ref: MoexBondRef, schedule: BondSchedule, *, price_to_put: bool = True
) -> Bond:
    """Build a :class:`cdslib.Bond` from reference data + coupon schedule.

    Cashflows run to the *effective maturity* — the offer/put date when
    ``price_to_put`` and an offer exists (post-offer coupons are unknown for
    these bonds), otherwise the legal maturity. Known coupons up to that date
    are used; the outstanding notional is redeemed at the effective maturity.
    """
    if ref.face_value is None:
        raise ValueError(f"{ref.isin}: missing face value")

    offer = min((o.date for o in schedule.offers if o.date is not None), default=None)
    effective = offer if (price_to_put and offer is not None) else ref.maturity_date
    if effective is None:
        raise ValueError(f"{ref.isin}: missing maturity/offer date")

    coupons: dict[date, float] = {}
    for coupon in schedule.coupons:
        if coupon.date is None or coupon.value is None:
            continue
        if coupon.date <= effective:
            coupons[coupon.date] = coupons.get(coupon.date, 0.0) + coupon.value

    principals: dict[date, float] = {}
    repaid = 0.0
    for amort in schedule.amortizations:
        if amort.date is None or amort.value is None:
            continue
        if amort.date < effective:
            principals[amort.date] = principals.get(amort.date, 0.0) + amort.value
            repaid += amort.value
    principals[effective] = principals.get(effective, 0.0) + (ref.face_value - repaid)

    dates = sorted(set(coupons) | set(principals))
    cashflows = tuple(
        BondCashflow(d, coupons.get(d, 0.0), principals.get(d, 0.0)) for d in dates
    )
    starts = [c.start_date for c in schedule.coupons if c.start_date is not None]
    first_accrual = min(starts) if starts else ref.issue_date
    return Bond(ref.isin, ref.face_value, cashflows, first_accrual_date=first_accrual)


# --- client ---------------------------------------------------------------


class MoexClient:
    """Thin synchronous client over the MOEX ISS API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client if client is not None else httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query: dict[str, Any] = {"iss.meta": "off"}
        query.update(params or {})
        response = self._client.get(self._base + path, params=query)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    def search(self, query: str) -> list[dict[str, Any]]:
        return rows(self._get("/securities.json", {"q": query}), "securities")

    def security(self, isin: str) -> MoexBondRef:
        return parse_security(self._get(f"/securities/{isin}.json"))

    def bondization(self, isin: str) -> BondSchedule:
        return parse_bondization(
            self._get(f"/securities/{isin}/bondization.json", {"limit": 1000})
        )

    def live_quote(self, isin: str, board: str = DEFAULT_BOARD) -> PriceQuote:
        payload = self._get(
            f"/engines/stock/markets/bonds/securities/{isin}.json",
            {"iss.only": "securities,marketdata"},
        )
        return parse_live_quote(payload, date.today(), board)

    def history_quote(
        self, isin: str, on: date, board: str = DEFAULT_BOARD
    ) -> PriceQuote:
        payload = self._get(
            f"/history/engines/stock/markets/bonds/securities/{isin}.json",
            {"from": on.isoformat(), "till": on.isoformat()},
        )
        return parse_history_quote(payload, on, board)

    def quote(
        self, isin: str, trade_date: date | None = None, board: str = DEFAULT_BOARD
    ) -> PriceQuote:
        if trade_date is None or trade_date >= date.today():
            return self.live_quote(isin, board)
        return self.history_quote(isin, trade_date, board)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MoexClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def bonds_from_refs(
    entries: Iterable[tuple[MoexBondRef, BondSchedule]], *, price_to_put: bool = True
) -> list[Bond]:
    """Convenience: build cdslib bonds for several (ref, schedule) pairs."""
    return [build_bond(ref, sched, price_to_put=price_to_put) for ref, sched in entries]
