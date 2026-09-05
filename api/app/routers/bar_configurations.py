"""Stored Alpaca bar-configuration API."""

from __future__ import annotations

from fastapi import APIRouter, Body, Query

from ..errors import api_http_error, not_found
from ..schemas import (
    BarConfigurationCreateRequest,
    BarConfigurationResolutionResponse,
    BarConfigurationResolveRequest,
    BarConfigurationResponse,
    BarConfigurationUpdateAcceptedResponse,
    BarConfigurationUpdateActionRequest,
    BarConfigurationUpdateRequest,
    ResourceCollection,
    ResourceDiscoveryResponse,
)
from ..services.bar_configurations import (
    create_configuration,
    delete_configuration,
    get_configuration,
    list_configurations,
    resolve_configuration,
    submit_configuration_update,
    update_configuration,
)
from ..services.common import resource_discovery, validate_ordering, validate_page_window

router = APIRouter(prefix="/v1/market-data/bar-configurations", tags=["Market Data"])


@router.get("", response_model=ResourceCollection)
def configurations_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    enabled: bool | None = None,
    asset_source: str | None = None,
    ordering: str = "name",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"name", "updated_at", "frequency_id"})
    try:
        return list_configurations(
            limit=limit,
            offset=offset,
            search=search,
            enabled=enabled,
            asset_source=asset_source,
            ordering=ordering,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def configurations_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="alpaca-bar-configurations",
        label="Alpaca Bar Configurations",
        item_label="configuration",
        identity_fields=["uid"],
        searchable_fields=["name", "description"],
        filterable_fields=["enabled", "asset_source"],
        filter_types={"enabled": "boolean"},
        filter_options={
            "asset_source": [
                {"value": "account_holdings", "label": "Latest account holdings"},
                {"value": "universe", "label": "Universe assets"},
                {"value": "assets", "label": "Explicit assets"},
            ]
        },
        orderable_fields=["name", "updated_at", "frequency_id"],
        columns=[
            {"id": "name", "header": "Configuration", "sortable_key": "name", "hideable": False},
            {"id": "asset_source", "header": "Asset Source", "filter_key": "asset_source"},
            {"id": "frequency_id", "header": "Frequency", "sortable_key": "frequency_id"},
            {"id": "feed", "header": "Feed"},
            {"id": "adjustment", "header": "Adjustment"},
            {"id": "enabled", "header": "Enabled", "data_type": "boolean", "filter_key": "enabled"},
            {
                "id": "updated_at",
                "header": "Updated",
                "data_type": "datetime",
                "sortable_key": "updated_at",
            },
        ],
    )


@router.post("", response_model=BarConfigurationResponse, status_code=201)
def configuration_create(
    request: BarConfigurationCreateRequest = Body(...),
) -> BarConfigurationResponse:
    try:
        return create_configuration(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{configuration_uid}", response_model=BarConfigurationResponse)
def configuration_get(configuration_uid: str) -> BarConfigurationResponse:
    item = get_configuration(configuration_uid)
    if item is None:
        raise not_found("Bar configuration not found.")
    return item


@router.patch("/{configuration_uid}", response_model=BarConfigurationResponse)
def configuration_update(
    configuration_uid: str,
    request: BarConfigurationUpdateRequest = Body(...),
) -> BarConfigurationResponse:
    try:
        return update_configuration(configuration_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/{configuration_uid}", response_model=dict)
def configuration_delete(configuration_uid: str) -> dict:
    try:
        return delete_configuration(configuration_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/{configuration_uid}/actions/resolve",
    response_model=BarConfigurationResolutionResponse,
)
def configuration_resolve(
    configuration_uid: str,
    request: BarConfigurationResolveRequest = Body(default=BarConfigurationResolveRequest()),
) -> BarConfigurationResolutionResponse:
    try:
        return resolve_configuration(configuration_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/{configuration_uid}/actions/update",
    response_model=BarConfigurationUpdateAcceptedResponse,
    status_code=202,
)
def configuration_update_submit(
    configuration_uid: str,
    _request: BarConfigurationUpdateActionRequest = Body(
        default=BarConfigurationUpdateActionRequest()
    ),
) -> BarConfigurationUpdateAcceptedResponse:
    try:
        return submit_configuration_update(configuration_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


__all__ = ["router"]
