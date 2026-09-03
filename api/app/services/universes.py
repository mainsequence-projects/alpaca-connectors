"""API shaping for materialized AssetCategory universes."""

from __future__ import annotations

from src.universes import (
    create_materialized_universe_configuration,
    delete_materialized_universe,
    get_materialized_universe,
    list_materialized_universes,
    preview_materialized_universe,
    run_materialized_universe,
    update_materialized_universe,
)

from ..schemas import (
    MaterializedUniverseCreateRequest,
    MaterializedUniverseResponse,
    MaterializedUniverseUpdateRequest,
)
from .common import collection_response, json_safe


def list_universes(*, limit: int, offset: int, search: str | None, ordering: str):
    items, total = list_materialized_universes(
        limit=limit,
        offset=offset,
        search=search,
        ordering=ordering,
    )
    return collection_response(items=items, total=total, limit=limit, offset=offset)


def get_universe(category_uid: str) -> MaterializedUniverseResponse | None:
    item = get_materialized_universe(category_uid)
    return MaterializedUniverseResponse.model_validate(item) if item else None


def create_universe(
    request: MaterializedUniverseCreateRequest,
) -> MaterializedUniverseResponse:
    item = create_materialized_universe_configuration(**request.model_dump())
    return MaterializedUniverseResponse.model_validate(item)


def preview_universe_run(category_uid: str, *, timeout: float) -> dict:
    universe = get_universe(category_uid)
    if universe is None:
        raise LookupError(f"Universe {category_uid} does not exist.")
    plan = preview_materialized_universe(category_uid, timeout=timeout)
    return {
        "universe": universe.model_dump(mode="json"),
        "plan_summary": json_safe(plan.summary()),
        "has_blockers": plan.has_blockers(),
    }


def run_universe(category_uid: str, *, timeout: float) -> dict:
    universe = get_universe(category_uid)
    if universe is None:
        raise LookupError(f"Universe {category_uid} does not exist.")
    result = run_materialized_universe(category_uid, timeout=timeout)
    return {
        "category_uid": category_uid,
        "source_uid": universe.source_uid,
        "unique_identifier": result.unique_identifier,
        "display_name": result.display_name,
        "asset_uids": [str(uid) for uid in result.asset_uids],
        "asset_count": len(result.asset_uids),
    }


def update_universe(
    category_uid: str,
    request: MaterializedUniverseUpdateRequest,
) -> MaterializedUniverseResponse:
    item = update_materialized_universe(
        category_uid,
        **request.model_dump(exclude_unset=True),
    )
    return MaterializedUniverseResponse.model_validate(item)


def delete_universe(category_uid: str) -> dict:
    return delete_materialized_universe(category_uid)


__all__ = [
    "create_universe",
    "delete_universe",
    "get_universe",
    "list_universes",
    "preview_universe_run",
    "run_universe",
    "update_universe",
]
