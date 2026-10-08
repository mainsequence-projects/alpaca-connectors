"""Assets API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Query, Response, status

from ..errors import api_http_error, not_found
from ..identity import acting_user_uid
from ..schemas import (
    AssetRegistrationDiscoveryResponse,
    AssetRegistrationExecuteResponse,
    AssetRegistrationOperationResponse,
    AssetRegistrationOperationStartRequest,
    AssetRegistrationRequest,
    ResourceCollection,
    ResourceDiscoveryResponse,
)
from ..services.assets import (
    build_asset_registration_discovery,
    execute_asset_registration,
    get_asset_registration_operation_status,
    get_asset_resource,
    list_asset_resources,
    run_asset_registration_operation,
    start_asset_registration_operation,
)
from ..services.common import resource_discovery, validate_ordering, validate_page_window

router = APIRouter(prefix="/v1/assets", tags=["Assets"])


@router.get("", response_model=ResourceCollection)
def assets_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    ordering: str = "ticker",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"ticker", "alpaca_asset_id"})
    try:
        return list_asset_resources(limit=limit, offset=offset, search=search, ordering=ordering)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def assets_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="alpaca-assets",
        label="Registered Alpaca assets",
        item_label="asset",
        identity_fields=["uid"],
        searchable_fields=["ticker", "name", "alpaca_asset_id", "unique_identifier"],
        orderable_fields=["ticker", "alpaca_asset_id"],
        columns=[
            {"id": "uid", "header": "UID", "hideable": False},
            {"id": "ticker", "header": "Ticker", "sortable_key": "ticker", "hideable": False},
            {"id": "name", "header": "Name", "data_type": "text"},
            {
                "id": "alpaca-asset-id",
                "header": "Alpaca Asset ID",
                "sortable_key": "alpaca_asset_id",
            },
            {"id": "exchange", "header": "Exchange"},
            {"id": "figi", "header": "FIGI (optional)"},
            {"id": "tradable", "header": "Trading", "data_type": "boolean"},
            {"id": "asset-type", "header": "Asset Type"},
        ],
    )


@router.post(
    "/registration/plan",
    response_model=AssetRegistrationDiscoveryResponse,
    summary="Plan asset registration",
    description="Resolve the existing registration plan without creating assets.",
)
def asset_registration_plan(
    request: AssetRegistrationRequest = Body(...),
) -> AssetRegistrationDiscoveryResponse:
    try:
        return build_asset_registration_discovery(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/registration/execute",
    response_model=AssetRegistrationExecuteResponse,
    summary="Execute asset registration",
    description=(
        "Create missing Main Sequence assets using immutable Alpaca asset IDs; OpenFIGI metadata "
        "is optional enrichment."
    ),
)
def asset_registration_execute(
    request: AssetRegistrationRequest = Body(...),
) -> AssetRegistrationExecuteResponse:
    try:
        return execute_asset_registration(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/registration/operations",
    response_model=AssetRegistrationOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an observable asset-registration operation",
    description=(
        "Persist an ordered registration-step record, run the requested plan or execution in the "
        "background, and return an operation UID for polling."
    ),
)
def asset_registration_operation_start(
    operation_request: AssetRegistrationOperationStartRequest,
    background_tasks: BackgroundTasks,
) -> AssetRegistrationOperationResponse:
    # The operation belongs to the acting user (the person an Agent works for, otherwise the
    # verified caller); only that user can poll it.
    owner_uid = acting_user_uid()
    try:
        operation = start_asset_registration_operation(
            action=operation_request.action,
            request=operation_request.request,
            owner_uid=owner_uid,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc
    background_tasks.add_task(
        run_asset_registration_operation,
        operation.operation_uid,
        action=operation_request.action,
        request=operation_request.request,
    )
    return operation


@router.get(
    "/registration/operations/{operation_uid}",
    response_model=AssetRegistrationOperationResponse,
    summary="Read asset-registration operation progress",
)
def asset_registration_operation_get(
    operation_uid: str,
    response: Response,
) -> AssetRegistrationOperationResponse:
    response.headers["Cache-Control"] = "no-store"
    owner_uid = acting_user_uid()
    try:
        return get_asset_registration_operation_status(operation_uid, owner_uid=owner_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{asset_uid}", response_model=dict[str, Any])
def asset_get(asset_uid: str) -> dict[str, Any]:
    asset = get_asset_resource(asset_uid)
    if asset is None:
        raise not_found("Asset not found.")
    return asset
