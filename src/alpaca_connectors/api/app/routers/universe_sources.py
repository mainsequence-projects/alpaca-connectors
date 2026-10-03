"""User-maintained universe-source API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from ..errors import api_http_error, not_found
from ..schemas import (
    ResourceCollection,
    ResourceDiscoveryResponse,
    UniverseSourceActionRequest,
    UniverseSourceCreateRequest,
    UniverseSourcePreviewResponse,
    UniverseSourceResponse,
    UniverseSourceUpdateRequest,
)
from ..services.common import (
    resource_discovery,
    validate_ordering,
    validate_page_window,
)
from ..services.universe_sources import (
    create_source,
    delete_source,
    get_source,
    list_sources,
    preview_source,
    update_source,
)

router = APIRouter(prefix="/v1/universe-sources", tags=["Universe Sources"])


@router.get("", response_model=ResourceCollection)
def sources_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    enabled: bool | None = None,
    ordering: str = "name",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"name", "symbol", "updated_at"})
    try:
        return list_sources(
            limit=limit,
            offset=offset,
            search=search,
            enabled=enabled,
            ordering=ordering,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def sources_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="universe-sources",
        label="Universe Sources",
        item_label="source",
        identity_fields=["uid"],
        searchable_fields=["name", "symbol", "source_url"],
        filterable_fields=["enabled"],
        filter_types={"enabled": "boolean"},
        orderable_fields=["name", "symbol", "updated_at"],
        columns=[
            {"id": "name", "header": "Source", "sortable_key": "name", "hideable": False},
            {"id": "symbol", "header": "Symbol", "sortable_key": "symbol"},
            {"id": "source_url", "header": "Source URL"},
            {"id": "enabled", "header": "Enabled", "data_type": "boolean", "filter_key": "enabled"},
            {
                "id": "updated_at",
                "header": "Updated",
                "data_type": "datetime",
                "sortable_key": "updated_at",
            },
        ],
        actions=[],
    )


@router.post("", response_model=UniverseSourceResponse, status_code=201)
def source_create(request: UniverseSourceCreateRequest = Body(...)) -> UniverseSourceResponse:
    try:
        return create_source(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{source_uid}", response_model=UniverseSourceResponse)
def source_get(source_uid: str) -> UniverseSourceResponse:
    source = get_source(source_uid)
    if source is None:
        raise not_found("Universe source not found.")
    return source


@router.patch("/{source_uid}", response_model=UniverseSourceResponse)
def source_update(
    source_uid: str,
    request: UniverseSourceUpdateRequest = Body(...),
) -> UniverseSourceResponse:
    try:
        return update_source(source_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/{source_uid}", response_model=dict[str, Any])
def source_delete(source_uid: str) -> dict[str, Any]:
    try:
        return delete_source(source_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/{source_uid}/actions/preview",
    response_model=UniverseSourcePreviewResponse,
)
def source_preview(
    source_uid: str,
    request: UniverseSourceActionRequest = Body(default=UniverseSourceActionRequest()),
) -> UniverseSourcePreviewResponse:
    try:
        return preview_source(source_uid, timeout=request.timeout)
    except Exception as exc:
        raise api_http_error(exc) from exc
