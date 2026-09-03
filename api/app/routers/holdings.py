"""Immutable account-holdings API."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Body, Query

from ..errors import api_http_error, not_found
from ..schemas import (
    HoldingsCaptureRequest,
    HoldingsCaptureResponse,
    ResourceCollection,
    ResourceDiscoveryResponse,
)
from ..services.common import resource_discovery, validate_ordering, validate_page_window
from ..services.holdings import (
    capture_holdings,
    get_holdings_snapshot,
    list_holdings,
    preflight_holdings,
)

router = APIRouter(prefix="/v1/accounts/{account_uid}/holdings", tags=["Holdings"])


@router.get("", response_model=ResourceCollection)
def holdings_list(
    account_uid: str,
    limit: int = Query(default=25, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    as_of: dt.datetime | None = None,
    asset_identifier: list[str] | None = Query(default=None),
    ordering: str = "-time_index",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"time_index", "asset_identifier"})
    try:
        return list_holdings(
            account_uid,
            limit=limit,
            offset=offset,
            as_of=as_of,
            asset_identifiers=asset_identifier,
            ordering=ordering,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def holdings_discovery(account_uid: str) -> ResourceDiscoveryResponse:
    del account_uid
    return resource_discovery(
        resource_id="alpaca-account-holdings",
        label="Account Holdings",
        item_label="holding",
        identity_fields=["time_index", "account_uid", "asset_identifier"],
        filterable_fields=["asset_identifier"],
        orderable_fields=["time_index", "asset_identifier"],
        columns=[
            {
                "id": "time_index",
                "header": "Time",
                "data_type": "datetime",
                "sortable_key": "time_index",
                "hideable": False,
            },
            {"id": "asset_identifier", "header": "Asset", "sortable_key": "asset_identifier"},
            {"id": "quantity", "header": "Quantity", "data_type": "number"},
            {"id": "direction", "header": "Direction", "data_type": "number"},
            {"id": "holdings_set_uid", "header": "Snapshot UID"},
        ],
    )


@router.post("/actions/capture/preflight", response_model=dict[str, Any])
def holdings_capture_preflight(account_uid: str) -> dict[str, Any]:
    try:
        return preflight_holdings(account_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/actions/capture", response_model=HoldingsCaptureResponse)
def holdings_capture(
    account_uid: str,
    request: HoldingsCaptureRequest = Body(default=HoldingsCaptureRequest()),
) -> HoldingsCaptureResponse:
    try:
        return HoldingsCaptureResponse.model_validate(
            capture_holdings(
                account_uid,
                register_missing_assets=request.register_missing_assets,
            )
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{holdings_set_uid}", response_model=dict[str, Any])
def holdings_get(account_uid: str, holdings_set_uid: str) -> dict[str, Any]:
    snapshot = get_holdings_snapshot(account_uid, holdings_set_uid)
    if snapshot is None:
        raise not_found("Holdings snapshot not found.")
    return snapshot
