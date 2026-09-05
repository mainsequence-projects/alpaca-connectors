from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .routers.accounts import router as accounts_router
from .routers.assets import router as assets_router
from .routers.bar_configurations import router as bar_configurations_router
from .routers.holdings import router as holdings_router
from .routers.market_data import router as market_data_router
from .routers.operations import router as operations_router
from .routers.portfolio_configurations import router as portfolio_configurations_router
from .routers.project_state import router as project_state_router
from .routers.signal_jobs import router as signal_jobs_router
from .routers.universe_sources import router as universe_sources_router
from .routers.universes import router as universes_router


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Attach the ms-markets runtime for the API process lifetime."""
    from src.runtime import start_markets_engine

    start_markets_engine()
    yield


app = FastAPI(
    title="Alpaca Connectors API",
    version="0.1.28",
    description=("Capability-oriented API for the existing Alpaca Connectors project behavior."),
    lifespan=_lifespan,
)


@app.exception_handler(Exception)
async def sanitized_unhandled_error(_request: Request, _exc: Exception) -> JSONResponse:
    """Keep dependency exception internals out of API responses and server exception logging."""
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "dependency_unavailable",
                "message": "The operation could not be completed by a required service.",
                "retryable": True,
            }
        },
    )


@app.middleware("http")
async def response_boundary_headers(request: Request, call_next):
    """Apply cache boundaries for caller-specific discovery and live operation state."""
    response = await call_next(request)
    if request.url.path.endswith("/discovery"):
        user_uid = str(getattr(request.state, "user_uid", "anonymous"))
        digest = sha256(f"{app.version}:{request.url.path}:{user_uid}".encode()).hexdigest()
        response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
        response.headers["ETag"] = f'"{digest}"'
        response.headers["Vary"] = "Authorization, X-Resource-Release-UID"
    elif request.url.path.startswith("/v1/operations/job-runs/") or request.url.path.endswith(
        "/observations"
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


cors_origins = [
    origin.strip()
    for origin in os.getenv("ALPACA_CONNECTORS_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if cors_origins:
    exact_origins = [origin for origin in cors_origins if "*" not in origin]
    wildcard_origins = [origin for origin in cors_origins if "*" in origin]
    origin_regex = (
        "^(?:"
        + "|".join(re.escape(origin).replace(r"\*", "[^.]+") for origin in wildcard_origins)
        + ")$"
        if wildcard_origins
        else None
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=exact_origins,
        allow_origin_regex=origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Resource-Release-UID"],
    )

app.include_router(project_state_router)
app.include_router(assets_router)
app.include_router(accounts_router)
app.include_router(holdings_router)
app.include_router(universe_sources_router)
app.include_router(universes_router)
app.include_router(market_data_router)
app.include_router(bar_configurations_router)
app.include_router(signal_jobs_router)
app.include_router(portfolio_configurations_router)
app.include_router(operations_router)
