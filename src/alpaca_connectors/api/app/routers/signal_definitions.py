"""Signal definitions API: an account's signals, whose weights its application provides.

The one kind of signal this connector makes (ADR 0028, decision 5): a definition belongs to one
account and is created, changed, given weights or deleted only by the account's owner. Only the
account's portfolios follow it, as many as it wants.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Query

from ..errors import api_http_error
from ..identity import acting_owner
from ..schemas import (
    PortfolioWeightsRequest,
    PublishedWeightsResponse,
    ResourceCollection,
    ResourceDiscoveryResponse,
    SignalDefinitionCreateRequest,
    SignalDefinitionResponse,
    SignalDefinitionUpdateRequest,
    SignalObservationsResponse,
)
from ..services.common import resource_discovery, validate_page_window
from ..services.signal_definitions import (
    create_definition,
    definition_weights,
    delete_definition,
    get_definition,
    give_definition_weights,
    list_definitions,
    update_definition,
)

router = APIRouter(prefix="/v1/signal-definitions", tags=["Portfolios"])


@router.get("", response_model=ResourceCollection)
def signal_definitions_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    account_uid: str | None = None,
    uid: list[uuid.UUID] | None = Query(
        default=None, description="Only these signal definitions; repeat the parameter for each."
    ),
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    try:
        return list_definitions(
            limit=limit,
            offset=offset,
            search=search,
            account_uid=account_uid,
            owner=acting_owner(),
            uids=[str(value) for value in uid] if uid is not None else None,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def signal_definitions_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="alpaca-signal-definitions",
        label="Signals",
        item_label="signal definition",
        identity_fields=["uid"],
        searchable_fields=["name", "key"],
        filterable_fields=["account_uid"],
        orderable_fields=["name"],
        columns=[
            {"id": "name", "header": "Signal", "sortable_key": "name", "hideable": False},
            {"id": "key", "header": "Key"},
            {"id": "account_uid", "header": "Account", "filter_key": "account_uid"},
            {"id": "portfolio_count", "header": "Portfolios", "data_type": "number"},
            {"id": "updated_at", "header": "Updated", "data_type": "datetime"},
        ],
    )


@router.post("", response_model=SignalDefinitionResponse, status_code=201)
def signal_definition_create(
    request: SignalDefinitionCreateRequest = Body(...),
) -> SignalDefinitionResponse:
    """Create a signal of an account the caller owns."""
    try:
        return create_definition(request, owner=acting_owner())
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{definition_uid}", response_model=SignalDefinitionResponse)
def signal_definition_get(definition_uid: str) -> SignalDefinitionResponse:
    try:
        return get_definition(definition_uid, owner=acting_owner())
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.patch("/{definition_uid}", response_model=SignalDefinitionResponse)
def signal_definition_update(
    definition_uid: str,
    request: SignalDefinitionUpdateRequest = Body(...),
) -> SignalDefinitionResponse:
    try:
        return update_definition(definition_uid, request, owner=acting_owner())
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/{definition_uid}", response_model=dict)
def signal_definition_delete(definition_uid: str) -> dict:
    """Delete a signal no portfolio follows, with its observations unless a retired one keeps them."""
    try:
        return delete_definition(definition_uid, owner=acting_owner())
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/{definition_uid}/weights", response_model=PublishedWeightsResponse, status_code=201)
def signal_definition_give_weights(
    definition_uid: str,
    request: PortfolioWeightsRequest = Body(...),
) -> PublishedWeightsResponse:
    """Publish new weights to the signal; every portfolio that follows it uses them."""
    try:
        return give_definition_weights(definition_uid, request, owner=acting_owner())
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{definition_uid}/weights", response_model=SignalObservationsResponse)
def signal_definition_weights(
    definition_uid: str,
    limit: int = Query(default=20, ge=1, le=500),
) -> SignalObservationsResponse:
    try:
        return definition_weights(definition_uid, limit=limit, owner=acting_owner())
    except Exception as exc:
        raise api_http_error(exc) from exc
