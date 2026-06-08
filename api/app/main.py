from __future__ import annotations

import datetime as dt
import os

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .command_center_models import DataNodeTableSourceInputResponse

from .schemas import (
    AssetRegistrationRequest,
    AssetRegistrationByTickerRequest,
    AssetRegistrationByTickerResponse,
    HealthResponse,
    HoldingsCategoryRequest,
    LightweightOhlcChartRequest,
    LightweightOhlcResponse,
)
from .services import (
    DEFAULT_CHART_ASSET_CATEGORY_UNIQUE_IDENTIFIER,
    execute_asset_registration_by_ticker,
    execute_asset_registration,
    execute_holdings_category_sync,
    execute_lightweight_ohlc_chart,
    get_discovery_config,
    resolve_lightweight_ohlc_asset_unique_identifier,
    search_assets_for_lightweight_ohlc_select,
)

app = FastAPI(
    title="Alpaca Connectors API",
    version="0.1.0",
    description=(
        "Automation API for Alpaca-backed asset discovery, asset registration, "
        "and holdings-based category management."
    ),
)


@app.on_event("startup")
def _attach_markets_runtime() -> None:
    """Attach the ms-markets runtime once when the API process starts.

    The OHLC chart read and asset search query already-migrated/registered ms-markets MetaTables,
    which requires ``msm.start_engine(...)`` first. Runs under uvicorn/lifespan; the unit tests
    construct ``TestClient(app)`` without entering its context manager, so this does not fire there.
    """
    from src.runtime import start_markets_engine

    start_markets_engine()


def _cors_allow_origins() -> list[str]:
    raw_origins = os.getenv("API_CORS_ALLOW_ORIGINS", "http://localhost:5173")
    origins = [
        origin.strip()
        for origin in raw_origins.split(",")
        if origin.strip()
    ]
    return origins or ["http://localhost:5173"]


_cors_origins = _cors_allow_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
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


@router.post(
    "/charts/lightweight/ohlc",
    response_model=LightweightOhlcResponse,
    response_model_exclude_none=True,
    summary="Build lightweight OHLC chart spec",
    description=(
        "Search registered assets for the Command Center ticker selector, or fetch "
        "OHLC data for one asset unique_identifier and return a lightweight-charts-spec "
        "payload suitable for Command Center rendering."
    ),
)
def lightweight_ohlc_chart(
    ticker: str | None = Query(
        default=None,
        description=(
            "Ticker, name, FIGI, or unique identifier. When dates are present, "
            "this value is resolved to the asset unique_identifier."
        ),
    ),
    start_date: dt.date | None = Query(
        default=None,
        description="Inclusive start date used by the Command Center chart form.",
    ),
    end_date: dt.date | None = Query(
        default=None,
        description="Inclusive end date used by the Command Center chart form.",
    ),
    node_identifier: str = Query(
        default="alpaca_stock_bars_1d_sip_all",
        description="DataNode identifier that backs the OHLC chart.",
        include_in_schema=False,
    ),
    asset_category_unique_identifier: str = Query(
        default=DEFAULT_CHART_ASSET_CATEGORY_UNIQUE_IDENTIFIER,
        description="AssetCategory used to scope ticker search options.",
        include_in_schema=False,
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="Search result page for the async selector.",
        include_in_schema=False,
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=50,
        description="Search result page size for the async selector.",
        include_in_schema=False,
    ),
    asset_search: str | None = Query(
        default=None,
        include_in_schema=False,
    ),
) -> LightweightOhlcResponse:
    try:
        ticker_query = ticker or asset_search or ""
        if start_date is None and end_date is None:
            return LightweightOhlcResponse.from_selector(
                search_assets_for_lightweight_ohlc_select(
                    query=ticker_query,
                    asset_category_unique_identifier=asset_category_unique_identifier,
                    page=page,
                    limit=limit,
                )
            )

        if not ticker_query or start_date is None or end_date is None:
            raise ValueError("Provide ticker, start_date, and end_date.")
        unique_identifier = resolve_lightweight_ohlc_asset_unique_identifier(
            identifier=ticker_query,
            asset_category_unique_identifier=asset_category_unique_identifier,
        )
        chart_request = LightweightOhlcChartRequest(
            unique_identifier=unique_identifier,
            start_date=start_date,
            end_date=end_date,
            node_identifier=node_identifier,
        )

        return LightweightOhlcResponse.from_chart(execute_lightweight_ohlc_chart(chart_request))
    except (ValueError, RuntimeError) as exc:
        raise _bad_request(exc) from exc


app.include_router(router)
