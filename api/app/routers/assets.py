"""Assets API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Query, Request, Response, status

from ..errors import api_http_error, not_found
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


def _request_user_uid(request: Request) -> str | None:
    value = getattr(request.state, "user_uid", None)
    return str(value) if value is not None else None


@router.get("", response_model=ResourceCollection)
def assets_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    ordering: str = "ticker",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"ticker", "unique_identifier"})
    try:
        return list_asset_resources(limit=limit, offset=offset, search=search, ordering=ordering)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def assets_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="alpaca-assets",
        label="Registered Assets",
        item_label="asset",
        identity_fields=["uid"],
        searchable_fields=["ticker", "name", "unique_identifier"],
        orderable_fields=["ticker", "unique_identifier"],
        columns=[
            {"id": "ticker", "header": "Ticker", "sortable_key": "ticker", "hideable": False},
            {"id": "name", "header": "Name"},
            {
                "id": "unique_identifier",
                "header": "Identifier",
                "sortable_key": "unique_identifier",
            },
            {"id": "asset_type", "header": "Asset Type"},
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
    description="Create missing Main Sequence assets after strict Alpaca and FIGI resolution.",
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
    request: Request,
) -> AssetRegistrationOperationResponse:
    try:
        operation = start_asset_registration_operation(
            action=operation_request.action,
            request=operation_request.request,
            owner_uid=_request_user_uid(request),
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
    request: Request,
    response: Response,
) -> AssetRegistrationOperationResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return get_asset_registration_operation_status(
            operation_uid,
            owner_uid=_request_user_uid(request),
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{asset_uid}", response_model=dict[str, Any])
def asset_get(asset_uid: str) -> dict[str, Any]:
    asset = get_asset_resource(asset_uid)
    if asset is None:
        raise not_found("Asset not found.")
    return asset
