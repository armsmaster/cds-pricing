from __future__ import annotations

from datetime import date

from pydantic import AliasChoices, BaseModel, Field


class QuoteIn(BaseModel):
    """A single OIS quote as pasted by the user (bid/ask in percentage points)."""

    rate: str = Field(
        validation_alias=AliasChoices("rate", "rate_name", "rate_index", "index")
    )
    tenor: str = Field(validation_alias=AliasChoices("tenor", "tenor_code", "code"))
    bid: float
    ask: float


class QuotesRequest(BaseModel):
    quotes: list[QuoteIn]
    trade_date: date | None = None


class BootstrapRequest(QuotesRequest):
    max_adjustment_bps: float = 15.0


class IssuerIn(BaseModel):
    name: str
    recovery_rate: float = 0.40
    moex_emitent_id: int | None = None


class RecoveryPatch(BaseModel):
    recovery_rate: float


class AddBondRequest(BaseModel):
    isin: str


class CreditCurveRequest(BaseModel):
    issuer_id: int
    rate_curve_id: int
    trade_date: date | None = None


class PriceOverride(BaseModel):
    override_clean: float | None = None
    included: bool = True
