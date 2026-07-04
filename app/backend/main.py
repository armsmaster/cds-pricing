from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.backend import service
from app.backend.schemas import BootstrapRequest, QuotesRequest

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="OIS Curve Bootstrapping")


@app.middleware("http")
async def no_cache_static(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_FRONTEND / "index.html")


app.mount("/", StaticFiles(directory=_FRONTEND), name="static")
