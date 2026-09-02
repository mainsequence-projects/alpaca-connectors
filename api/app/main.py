from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Body, FastAPI, HTTPException

from .schemas import (
    AssetRegistrationExecuteResponse,
    AssetRegistrationRequest,
    DiscoveryConfigResponse,
    HealthResponse,
    HoldingsCategoryExecuteResponse,
    HoldingsCategoryRequest,
)
from .services import (
    execute_asset_registration,
    execute_holdings_category_sync,
    get_discovery_config,
)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Attach the ms-markets runtime for the API process lifetime."""
    from src.runtime import start_markets_engine

    start_markets_engine()
    yield


app = FastAPI(
    title="Alpaca Connectors API",
    version="0.1.0",
    description=(
        "Automation API for Alpaca-backed asset discovery, asset registration, "
        "and holdings-based category management."
    ),
    lifespan=_lifespan,
)


router = APIRouter(prefix="/v1")


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="API health",
    description="Return a minimal health payload for the Alpaca Connectors API.",
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/discovery/config",
    response_model=DiscoveryConfigResponse,
    summary="Discovery configuration",
    description=(
        "Return supported holdings providers plus the configured seed universes "
        "and ETF-to-provider mapping."
    ),
)
def discovery_config() -> DiscoveryConfigResponse:
    return get_discovery_config()


@router.post(
    "/assets/registration/execute",
    response_model=AssetRegistrationExecuteResponse,
    summary="Execute asset registration",
    description=(
        "Run the Alpaca-backed FIGI registration flow and create any missing MainSequence "
        "public assets."
    ),
)
def asset_registration_execute(
    request: AssetRegistrationRequest = Body(
        ...,
        description="Asset registration execution request.",
    ),
) -> AssetRegistrationExecuteResponse:
    try:
        return execute_asset_registration(request)
    except (ValueError, RuntimeError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/holdings-categories/execute",
    response_model=HoldingsCategoryExecuteResponse,
    summary="Execute holdings category sync",
    description=(
        "Create or refresh a holdings-based MainSequence AssetCategory after the strict "
        "validation passes."
    ),
)
def holdings_category_execute(
    request: HoldingsCategoryRequest = Body(
        ...,
        description="Holdings category execution request.",
    ),
) -> HoldingsCategoryExecuteResponse:
    try:
        return execute_holdings_category_sync(request)
    except (ValueError, RuntimeError) as exc:
        raise _bad_request(exc) from exc


app.include_router(router)
