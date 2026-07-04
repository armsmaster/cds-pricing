from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.backend import credit_service, excel, repositories, service
from app.backend.db import get_session
from app.backend.moex import MoexClient
from app.backend.schemas import (
    AddBondRequest,
    BootstrapRequest,
    CreditCurveRequest,
    IssuerIn,
    PriceOverride,
    QuotesRequest,
    RecoveryPatch,
)

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"

app = FastAPI(title="OIS Curve & Credit Bootstrapping")


def get_moex() -> Iterator[MoexClient]:
    raw = os.environ.get("PCDS_MOEX_VERIFY")
    if raw is not None:
        verify_disable = raw.lower() in ("0", "false", "no")
        verify: bool | str = not verify_disable
    else:
        verify = _BUNDLE if os.path.isfile(_BUNDLE) else True
    client = MoexClient(verify=verify)
    try:
        yield client
    finally:
        client.close()


@app.middleware("http")
async def no_cache_static(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# --- rate curve (OIS) -----------------------------------------------------


@app.post("/api/quotes")
def quotes(request: QuotesRequest) -> dict[str, Any]:
    try:
        return service.preview(request.quotes, request.trade_date)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/bootstrap")
def bootstrap_endpoint(request: BootstrapRequest) -> dict[str, Any]:
    try:
        return service.run_bootstrap(
            request.quotes, request.trade_date, request.max_adjustment_bps
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/rate-curves")
def save_rate_curve(
    request: BootstrapRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        result = service.compute(
            request.quotes, request.trade_date, request.max_adjustment_bps
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = repositories.save_rate_curve(
        session,
        result.rate_index,
        result.curve,
        trade_date=result.trade_date,
        zcyc_json=None,
        max_adjustment_bps=request.max_adjustment_bps,
        average_adjustment_bps=result.average_adjustment_bps,
    )
    return credit_service.rate_curve_dict(record)


@app.get("/api/rate-curves")
def list_rate_curves(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [credit_service.rate_curve_dict(r) for r in repositories.list_rate_curves(session)]


@app.delete("/api/rate-curves/{curve_id}")
def delete_rate_curve(
    curve_id: int, session: Session = Depends(get_session)
) -> dict[str, str]:
    repositories.delete_rate_curve(session, curve_id)
    return {"status": "deleted"}


# --- issuers --------------------------------------------------------------


@app.post("/api/issuers")
def create_issuer(
    request: IssuerIn, session: Session = Depends(get_session)
) -> dict[str, Any]:
    issuer = repositories.create_issuer(
        session, request.name, request.recovery_rate, request.moex_emitent_id
    )
    return credit_service.issuer_dict(issuer)


@app.get("/api/issuers")
def list_issuers(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [credit_service.issuer_dict(i) for i in repositories.list_issuers(session)]


@app.patch("/api/issuers/{issuer_id}")
def patch_issuer(
    issuer_id: int, request: RecoveryPatch, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        issuer = repositories.set_recovery_rate(session, issuer_id, request.recovery_rate)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return credit_service.issuer_dict(issuer)


@app.delete("/api/issuers/{issuer_id}")
def delete_issuer(
    issuer_id: int, session: Session = Depends(get_session)
) -> dict[str, str]:
    repositories.delete_issuer(session, issuer_id)
    return {"status": "deleted"}


# --- bonds ----------------------------------------------------------------


@app.get("/api/moex/search")
def moex_search(
    q: str = Query(min_length=1), client: MoexClient = Depends(get_moex)
) -> list[dict[str, Any]]:
    try:
        return client.search(q)
    except Exception as exc:  # noqa: BLE001 - surface upstream failure
        raise HTTPException(status_code=502, detail=f"MOEX error: {exc}") from exc


@app.get("/api/issuers/{issuer_id}/bonds")
def list_bonds(
    issuer_id: int, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    return [credit_service.bond_dict(b) for b in repositories.list_bonds(session, issuer_id)]


@app.post("/api/issuers/{issuer_id}/bonds")
def add_bond(
    issuer_id: int,
    request: AddBondRequest,
    session: Session = Depends(get_session),
    client: MoexClient = Depends(get_moex),
) -> dict[str, Any]:
    if repositories.get_issuer(session, issuer_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown issuer {issuer_id}")
    try:
        bond = credit_service.add_bond(session, client, issuer_id, request.isin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - MOEX failure
        raise HTTPException(status_code=502, detail=f"MOEX error: {exc}") from exc
    return credit_service.bond_dict(bond)


@app.delete("/api/bonds/{isin}")
def delete_bond(isin: str, session: Session = Depends(get_session)) -> dict[str, str]:
    repositories.delete_bond(session, isin)
    return {"status": "deleted"}


@app.get("/api/bonds/{isin}/marketdata")
def bond_marketdata(
    isin: str,
    on: date | None = Query(default=None, alias="date"),
    session: Session = Depends(get_session),
    client: MoexClient = Depends(get_moex),
) -> dict[str, Any]:
    try:
        record = credit_service.fetch_market(session, client, isin, on or date.today())
    except Exception as exc:  # noqa: BLE001 - MOEX failure
        raise HTTPException(status_code=502, detail=f"MOEX error: {exc}") from exc
    return credit_service.market_dict(record)


# --- editable market prices ------------------------------------------------


@app.get("/api/issuers/{issuer_id}/prices")
def issuer_prices(
    issuer_id: int,
    on: date | None = Query(default=None, alias="date"),
    refresh: bool = Query(default=False),
    session: Session = Depends(get_session),
    client: MoexClient = Depends(get_moex),
) -> dict[str, Any]:
    try:
        return credit_service.issuer_prices(
            session, client, issuer_id, on or date.today(), force=refresh
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/bonds/{isin}/prices")
def patch_price(
    isin: str,
    request: PriceOverride,
    on: date | None = Query(default=None, alias="date"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return credit_service.set_override(
            session, isin, on or date.today(), request.override_clean, request.included
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- credit curve ---------------------------------------------------------


@app.post("/api/credit-curve")
def credit_curve_endpoint(
    request: CreditCurveRequest,
    session: Session = Depends(get_session),
    client: MoexClient = Depends(get_moex),
) -> dict[str, Any]:
    try:
        return credit_service.credit_curve(
            session, client, request.issuer_id, request.rate_curve_id, request.trade_date
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/credit-curve.xlsx")
def credit_curve_excel(
    issuer_id: int = Query(),
    rate_curve_id: int = Query(),
    on: date | None = Query(default=None, alias="trade_date"),
    session: Session = Depends(get_session),
    client: MoexClient = Depends(get_moex),
) -> Response:
    try:
        ctx = credit_service.compute_credit(session, client, issuer_id, rate_curve_id, on)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    content = excel.build_workbook(ctx)
    stem = f"credit_{ctx.issuer_name}_{ctx.trade.isoformat()}".replace(" ", "_")
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_FRONTEND / "index.html")


app.mount("/", StaticFiles(directory=_FRONTEND), name="static")
