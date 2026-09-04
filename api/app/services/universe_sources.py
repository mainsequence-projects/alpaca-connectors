"""API shaping for durable universe-source services."""

from __future__ import annotations

from src.universes import (
    create_universe_source,
    delete_universe_source,
    get_universe_source,
    list_universe_sources,
    preview_universe_source,
    update_universe_source,
)

from ..schemas import (
    UniverseSourceCreateRequest,
    UniverseSourcePreviewResponse,
    UniverseSourceResponse,
    UniverseSourceUpdateRequest,
)
from .common import collection_response, json_safe


def list_sources(
    *, limit: int, offset: int, search: str | None, enabled: bool | None, ordering: str
):
    items, total = list_universe_sources(
        limit=limit,
        offset=offset,
        search=search,
        enabled=enabled,
        ordering=ordering,
    )
    return collection_response(items=items, total=total, limit=limit, offset=offset)


def create_source(request: UniverseSourceCreateRequest) -> UniverseSourceResponse:
    return UniverseSourceResponse.model_validate(create_universe_source(**request.model_dump()))


def get_source(source_uid: str) -> UniverseSourceResponse | None:
    source = get_universe_source(source_uid)
    return UniverseSourceResponse.model_validate(source) if source else None


def update_source(
    source_uid: str,
    request: UniverseSourceUpdateRequest,
) -> UniverseSourceResponse:
    return UniverseSourceResponse.model_validate(
        update_universe_source(source_uid, **request.model_dump(exclude_unset=True))
    )


def delete_source(source_uid: str) -> dict:
    return delete_universe_source(source_uid)


def preview_source(source_uid: str, *, timeout: float) -> UniverseSourcePreviewResponse:
    source = get_source(source_uid)
    if source is None:
        raise LookupError(f"Universe source {source_uid} does not exist.")
    plan = preview_universe_source(source_uid, timeout=timeout)
    return UniverseSourcePreviewResponse(
        source=source,
        plan_summary=json_safe(plan.summary()),
        has_blockers=plan.has_blockers(),
    )


__all__ = [
    "create_source",
    "delete_source",
    "get_source",
    "list_sources",
    "preview_source",
    "update_source",
]
