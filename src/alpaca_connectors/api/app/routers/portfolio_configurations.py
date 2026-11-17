"""Durable analytical portfolio and rebalance configuration API.

ETF portfolios follow an ETF signal; provided-weights portfolios follow weights the calling
application gives (ADR 0024) and are changed only by their owner. Only provided-weights
portfolios are created; ETF portfolios are read, run and deleted until none is left (ADR 0027).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Query

from ..errors import api_http_error, not_found
from ..identity import acting_owner
from ..schemas import (
    PortfolioConfigurationDetailResponse,
    PortfolioConfigurationResponse,
    PortfolioConfigurationUpdateRequest,
    PortfolioJobRunAcceptedResponse,
    PortfolioJobRunResponse,
    PortfolioRebalanceConfigurationCreateRequest,
    PortfolioRebalanceConfigurationResponse,
    PortfolioRebalanceConfigurationUpdateRequest,
    PortfolioWeightsRequest,
    ProvidedWeightsPortfolioCreateRequest,
    ProvidedWeightsPortfolioCreateResponse,
    PublishedWeightsResponse,
    ResourceCollection,
    ResourceDiscoveryResponse,
    SignalObservationsResponse,
)
from ..services.common import resource_discovery, validate_ordering, validate_page_window
from ..services.portfolio_configurations import (
    configuration_runs,
    configuration_weights,
    create_provided_configuration,
    create_rebalance,
    delete_configuration,
    delete_rebalance,
    get_configuration_detail,
    get_rebalance,
    give_weights,
    list_configurations,
    list_rebalances,
    run_configuration,
    update_configuration,
    update_rebalance,
)

router = APIRouter(tags=["Portfolios"])


@router.get("/v1/portfolio-configurations", response_model=ResourceCollection)
def portfolio_configurations_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    signal_configuration_uid: str | None = None,
    bars_configuration_uid: str | None = None,
    weights_source: Literal["etf_signal", "provided"] | None = None,
    ordering: str = "name",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"name", "updated_at"})
    try:
        return list_configurations(
            limit=limit,
            offset=offset,
            search=search,
            signal_configuration_uid=signal_configuration_uid,
            bars_configuration_uid=bars_configuration_uid,
            weights_source=weights_source,
            ordering=ordering,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get(
    "/v1/portfolio-configurations/discovery",
    response_model=ResourceDiscoveryResponse,
)
def portfolio_configurations_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="alpaca-etf-portfolio-configurations",
        label="ETF Portfolios",
        item_label="portfolio configuration",
        identity_fields=["uid"],
        searchable_fields=["name", "description"],
        filterable_fields=["signal_configuration_uid", "bars_configuration_uid", "weights_source"],
        orderable_fields=["name", "updated_at"],
        columns=[
            {"id": "name", "header": "Portfolio", "sortable_key": "name", "hideable": False},
            {"id": "rebalance_strategy", "header": "Rebalance"},
            {
                "id": "job_image_status",
                "value_path": "job.image_status",
                "header": "Image",
            },
            {"id": "latest_run_status", "header": "Last Run"},
            {"id": "latest_run_at", "header": "Last Run At", "data_type": "datetime"},
            {"id": "updated_at", "header": "Updated", "data_type": "datetime"},
        ],
    )


@router.post(
    "/v1/portfolio-configurations/provided-weights",
    response_model=ProvidedWeightsPortfolioCreateResponse,
    status_code=201,
)
def provided_weights_portfolio_create(
    request: ProvidedWeightsPortfolioCreateRequest = Body(...),
) -> ProvidedWeightsPortfolioCreateResponse:
    """Create a portfolio from weights the caller provides; the caller's acting owner owns it."""
    try:
        return create_provided_configuration(request, owner=acting_owner())
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/v1/portfolio-configurations/{configuration_uid}/weights",
    response_model=PublishedWeightsResponse,
    status_code=201,
)
def provided_weights_give(
    configuration_uid: str,
    request: PortfolioWeightsRequest = Body(...),
) -> PublishedWeightsResponse:
    """Give the portfolio new weights; only its owner may."""
    try:
        return give_weights(configuration_uid, request, owner=acting_owner())
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get(
    "/v1/portfolio-configurations/{configuration_uid}/weights",
    response_model=SignalObservationsResponse,
)
def provided_weights_read(
    configuration_uid: str,
    limit: int = Query(default=100, ge=1, le=100),
) -> SignalObservationsResponse:
    try:
        return configuration_weights(configuration_uid, limit=limit)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get(
    "/v1/portfolio-configurations/{configuration_uid}",
    response_model=PortfolioConfigurationDetailResponse,
)
def portfolio_configuration_get(
    configuration_uid: str,
    observation_limit: int = Query(default=2_500, ge=1, le=5_000),
) -> PortfolioConfigurationDetailResponse:
    try:
        item = get_configuration_detail(
            configuration_uid,
            observation_limit=observation_limit,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc
    if item is None:
        raise not_found("Portfolio configuration not found.")
    return item


@router.patch(
    "/v1/portfolio-configurations/{configuration_uid}",
    response_model=PortfolioConfigurationResponse,
)
def portfolio_configuration_update(
    configuration_uid: str,
    request: PortfolioConfigurationUpdateRequest = Body(...),
) -> PortfolioConfigurationResponse:
    try:
        return update_configuration(configuration_uid, request, owner=acting_owner)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/v1/portfolio-configurations/{configuration_uid}", response_model=dict)
def portfolio_configuration_delete(configuration_uid: str) -> dict:
    try:
        return delete_configuration(configuration_uid, owner=acting_owner)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/v1/portfolio-configurations/{configuration_uid}/actions/run",
    response_model=PortfolioJobRunAcceptedResponse,
    status_code=202,
)
def portfolio_configuration_run(configuration_uid: str) -> PortfolioJobRunAcceptedResponse:
    try:
        return run_configuration(configuration_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get(
    "/v1/portfolio-configurations/{configuration_uid}/runs",
    response_model=list[PortfolioJobRunResponse],
)
def portfolio_configuration_runs(
    configuration_uid: str,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[PortfolioJobRunResponse]:
    try:
        return configuration_runs(configuration_uid, limit=limit)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/v1/portfolio-rebalance-configurations", response_model=ResourceCollection)
def rebalance_configurations_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    ordering: str = "name",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"name", "updated_at"})
    try:
        return list_rebalances(limit=limit, offset=offset, search=search, ordering=ordering)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get(
    "/v1/portfolio-rebalance-configurations/discovery",
    response_model=ResourceDiscoveryResponse,
)
def rebalance_configurations_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="portfolio-rebalance-configurations",
        label="Rebalance Configurations",
        item_label="rebalance configuration",
        identity_fields=["uid"],
        searchable_fields=["name", "description"],
        orderable_fields=["name", "updated_at"],
        columns=[
            {"id": "name", "header": "Configuration", "hideable": False},
            {"id": "strategy", "header": "Strategy"},
            {"id": "calendar_identifier", "header": "Calendar"},
            {"id": "rebalance_event", "header": "Market Event"},
            {"id": "rebalance_cadence", "header": "Cadence"},
            {"id": "updated_at", "header": "Updated", "data_type": "datetime"},
        ],
    )


@router.post(
    "/v1/portfolio-rebalance-configurations",
    response_model=PortfolioRebalanceConfigurationResponse,
    status_code=201,
)
def rebalance_configuration_create(
    request: PortfolioRebalanceConfigurationCreateRequest = Body(...),
) -> PortfolioRebalanceConfigurationResponse:
    try:
        return create_rebalance(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get(
    "/v1/portfolio-rebalance-configurations/{configuration_uid}",
    response_model=PortfolioRebalanceConfigurationResponse,
)
def rebalance_configuration_get(
    configuration_uid: str,
) -> PortfolioRebalanceConfigurationResponse:
    item = get_rebalance(configuration_uid)
    if item is None:
        raise not_found("Rebalance configuration not found.")
    return item


@router.patch(
    "/v1/portfolio-rebalance-configurations/{configuration_uid}",
    response_model=PortfolioRebalanceConfigurationResponse,
)
def rebalance_configuration_update(
    configuration_uid: str,
    request: PortfolioRebalanceConfigurationUpdateRequest = Body(...),
) -> PortfolioRebalanceConfigurationResponse:
    try:
        return update_rebalance(configuration_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete(
    "/v1/portfolio-rebalance-configurations/{configuration_uid}",
    response_model=dict,
)
def rebalance_configuration_delete(configuration_uid: str) -> dict:
    try:
        return delete_rebalance(configuration_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


__all__ = ["router"]
