"""Durable analytical ETF portfolio and rebalance configuration API."""

from __future__ import annotations

from fastapi import APIRouter, Body, Query

from ..errors import api_http_error, not_found
from ..schemas import (
    PortfolioConfigurationCreateRequest,
    PortfolioConfigurationResponse,
    PortfolioConfigurationUpdateRequest,
    PortfolioJobRunAcceptedResponse,
    PortfolioJobRunResponse,
    PortfolioRebalanceConfigurationCreateRequest,
    PortfolioRebalanceConfigurationResponse,
    PortfolioRebalanceConfigurationUpdateRequest,
    ResourceCollection,
    ResourceDiscoveryResponse,
)
from ..services.common import resource_discovery, validate_ordering, validate_page_window
from ..services.portfolio_configurations import (
    configuration_runs,
    create_configuration,
    create_rebalance,
    delete_configuration,
    delete_rebalance,
    get_configuration,
    get_rebalance,
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
        filterable_fields=["signal_configuration_uid", "bars_configuration_uid"],
        orderable_fields=["name", "updated_at"],
        columns=[
            {"id": "name", "header": "Portfolio", "sortable_key": "name", "hideable": False},
            {"id": "rebalance_strategy", "header": "Rebalance"},
            {"id": "portfolio_uid", "header": "Portfolio UID"},
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
    "/v1/portfolio-configurations",
    response_model=PortfolioConfigurationResponse,
    status_code=201,
)
def portfolio_configuration_create(
    request: PortfolioConfigurationCreateRequest = Body(...),
) -> PortfolioConfigurationResponse:
    try:
        return create_configuration(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get(
    "/v1/portfolio-configurations/{configuration_uid}",
    response_model=PortfolioConfigurationResponse,
)
def portfolio_configuration_get(configuration_uid: str) -> PortfolioConfigurationResponse:
    item = get_configuration(configuration_uid)
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
        return update_configuration(configuration_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/v1/portfolio-configurations/{configuration_uid}", response_model=dict)
def portfolio_configuration_delete(configuration_uid: str) -> dict:
    try:
        return delete_configuration(configuration_uid)
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
