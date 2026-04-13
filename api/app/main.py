from __future__ import annotations

from fastapi import APIRouter, Body, FastAPI, HTTPException

from .command_center_models import DataNodeTableSourceInputResponse

from .schemas import (
    AssetRegistrationRequest,
    AssetRegistrationByTickerRequest,
    AssetRegistrationByTickerResponse,
    HealthResponse,
    HoldingsCategoryRequest,
)
from .services import (
    build_asset_registration_discovery,
    execute_asset_registration_by_ticker,
    build_holdings_category_discovery,
    execute_asset_registration,
    execute_holdings_category_sync,
    get_discovery_config,
)

app = FastAPI(
    title="Alpaca Connectors API",
    version="0.1.0",
    description=(
        "Automation API for Alpaca-backed asset discovery, asset registration, "
        "and holdings-based category management."
    ),
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
    response_model=DataNodeTableSourceInputResponse,
    summary="Discovery configuration",
    description=(
        "Return supported holdings providers plus the configured seed universes "
        "and ETF-to-provider mapping."
    ),
)
def discovery_config() -> DataNodeTableSourceInputResponse:
    return get_discovery_config()


@router.post(
    "/assets/registration/plan",
    response_model=DataNodeTableSourceInputResponse,
    summary="Preview asset registration",
    description=(
        "Build the Alpaca and FIGI registration plan for exact symbols or an ETF seed "
        "universe without writing assets to MainSequence."
    ),
) 
def asset_registration_plan(
    request: AssetRegistrationRequest = Body(
        ...,
        description="Asset discovery or holdings-expansion request.",
    ),
) -> DataNodeTableSourceInputResponse:
    try:
        return build_asset_registration_discovery(request)
    except (ValueError, RuntimeError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/assets/registration/execute",
    response_model=DataNodeTableSourceInputResponse,
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
) -> DataNodeTableSourceInputResponse:
    try:
        return execute_asset_registration(request)
    except (ValueError, RuntimeError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/app-components/assets/register-ticker",
    response_model=AssetRegistrationByTickerResponse,
    summary="Register one asset by ticker",
    description=(
        "Resolve one ticker against Alpaca and FIGI, then register the corresponding "
        "MainSequence public asset if it is missing."
    ),
)
def asset_registration_execute_by_ticker(
    request: AssetRegistrationByTickerRequest = Body(
        ...,
        description="Single-ticker registration request for the AppComponent widget.",
    ),
) -> AssetRegistrationByTickerResponse:
    try:
        return execute_asset_registration_by_ticker(request)
    except (ValueError, RuntimeError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/holdings-categories/plan",
    response_model=DataNodeTableSourceInputResponse,
    summary="Preview holdings category sync",
    description=(
        "Extract ETF holdings, validate Alpaca and FIGI coverage, and preview the "
        "resulting holdings AssetCategory sync without writing to MainSequence."
    ),
) 
def holdings_category_plan(
    request: HoldingsCategoryRequest = Body(
        ...,
        description="Holdings category discovery request.",
    ),
) -> DataNodeTableSourceInputResponse:
    try:
        return build_holdings_category_discovery(request)
    except (ValueError, RuntimeError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/holdings-categories/execute",
    response_model=DataNodeTableSourceInputResponse,
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
) -> DataNodeTableSourceInputResponse:
    try:
        return execute_holdings_category_sync(request)
    except (ValueError, RuntimeError) as exc:
        raise _bad_request(exc) from exc


app.include_router(router)
