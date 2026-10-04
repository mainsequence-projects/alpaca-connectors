"""Alpaca Connectors FastAPI application.

`create_app()` builds the application and installs the Main Sequence request identity once. The
module's `app`, which the deployed entry point `api/app/main.py` re-exports, comes from it.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mainsequence.server.fastapi import install_request_identity

from .identity import bound_caller_uid
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

ROUTERS = (
    project_state_router,
    assets_router,
    accounts_router,
    holdings_router,
    universe_sources_router,
    universes_router,
    market_data_router,
    bar_configurations_router,
    signal_jobs_router,
    portfolio_configurations_router,
    operations_router,
)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Attach the ms-markets runtime for the API process lifetime."""
    from alpaca_connectors.runtime import start_markets_engine

    start_markets_engine()
    yield


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


async def redacted_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Report validation failures without echoing request input.

    FastAPI's default 422 body includes each rejected ``input``; account requests can carry
    Alpaca credential values, so only the location, message, and error type are returned.
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "loc": jsonable_encoder(error.get("loc", ())),
                    "msg": error.get("msg", ""),
                    "type": error.get("type", ""),
                }
                for error in exc.errors()
            ]
        },
    )


async def response_boundary_headers(request: Request, call_next):
    """Apply cache boundaries for caller-specific discovery and live operation state."""
    response = await call_next(request)
    if request.url.path.endswith("/discovery"):
        caller = bound_caller_uid() or "anonymous"
        digest = sha256(f"{request.app.version}:{request.url.path}:{caller}".encode()).hexdigest()
        response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
        response.headers["ETag"] = f'"{digest}"'
        response.headers["Vary"] = "Authorization, X-Resource-Release-UID"
    elif request.url.path.startswith("/v1/operations/job-runs/") or request.url.path.endswith(
        "/observations"
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


def _cors_options() -> dict[str, Any] | None:
    """CORS policy from ``ALPACA_CONNECTORS_CORS_ORIGINS``; ``*`` matches one hostname label."""
    origins = [
        origin.strip()
        for origin in os.getenv("ALPACA_CONNECTORS_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if not origins:
        return None
    wildcard_origins = [origin for origin in origins if "*" in origin]
    return {
        "allow_origins": [origin for origin in origins if "*" not in origin],
        "allow_origin_regex": (
            "^(?:"
            + "|".join(re.escape(origin).replace(r"\*", "[^.]+") for origin in wildcard_origins)
            + ")$"
            if wildcard_origins
            else None
        ),
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        "allow_headers": ["Authorization", "Content-Type", "X-Resource-Release-UID"],
    }


def create_app() -> FastAPI:
    """Create the API with the Main Sequence request identity installed.

    Every route except those the deployment declares in ``FASTAPI_PUBLIC_INGRESS`` (none) needs a
    verified caller: the platform-signed caller assertion when hosted, or a direct Bearer token
    checked against ``MAINSEQUENCE_ENDPOINT`` locally. Routes read it through
    ``alpaca_connectors.api.app.identity``. The platform's FastAPI launcher refuses to serve an
    application without this installation. Each call returns a new application with its own.
    """
    app = FastAPI(
        title="Alpaca Connectors API",
        version="0.1.28",
        description=(
            "Capability-oriented API for the existing Alpaca Connectors project behavior."
        ),
        lifespan=_lifespan,
    )
    app.add_exception_handler(Exception, sanitized_unhandled_error)
    app.add_exception_handler(RequestValidationError, redacted_validation_error)
    app.middleware("http")(response_boundary_headers)
    for router in ROUTERS:
        app.include_router(router)
    # Once, after every route is mounted; requests reach the middleware above, and the routes,
    # only with a verified caller.
    install_request_identity(app)
    # Outside the request identity, as the launcher's own CORS is in a deployment, so a browser on
    # an allowed origin can read the 401 or 503 that the identity answers.
    cors = _cors_options()
    if cors is not None:
        app.add_middleware(CORSMiddleware, **cors)
    return app


app = create_app()
