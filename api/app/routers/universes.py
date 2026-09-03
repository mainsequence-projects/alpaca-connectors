"""Materialized AssetCategory universe API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from ..errors import api_http_error, not_found
from ..schemas import (
    BulkActionRequest,
    MaterializedUniverseCreateRequest,
    MaterializedUniverseResponse,
    MaterializedUniverseUpdateRequest,
    ResourceCollection,
    ResourceDiscoveryResponse,
)
from ..services.common import (
    positive_float_option,
    resource_discovery,
    validate_ordering,
    validate_page_window,
)
from ..services.universes import (
    create_universe,
    delete_universe,
    get_universe,
    list_universes,
    preview_universe_run,
    run_universe,
    update_universe,
)

router = APIRouter(prefix="/v1/universes", tags=["Universes"])


@router.get("", response_model=ResourceCollection)
def universes_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    ordering: str = "display_name",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"display_name", "unique_identifier"})
    try:
        return list_universes(limit=limit, offset=offset, search=search, ordering=ordering)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("", response_model=MaterializedUniverseResponse, status_code=201)
def universe_create(
    request: MaterializedUniverseCreateRequest = Body(...),
) -> MaterializedUniverseResponse:
    try:
        return create_universe(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def universes_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="materialized-universes",
        label="Registered Universes",
        item_label="universe",
        identity_fields=["uid"],
        searchable_fields=["display_name", "unique_identifier"],
        orderable_fields=["display_name", "unique_identifier"],
        columns=[
            {
                "id": "display_name",
                "header": "Universe",
                "sortable_key": "display_name",
                "hideable": False,
            },
            {
                "id": "uid",
                "header": "UID",
            },
            {"id": "asset_count", "header": "Assets", "data_type": "number"},
            {"id": "is_active", "header": "Status", "data_type": "boolean"},
        ],
        actions=[
            {
                "id": "run",
                "label": "Run",
                "endpoint": "/v1/universes/actions/run",
                "method": "POST",
                "selection_modes": ["explicit"],
                "options": [],
                "preflight_endpoint": "/v1/universes/actions/run/preflight",
            },
            {
                "id": "activate",
                "label": "Activate",
                "endpoint": "/v1/universes/actions/activate",
                "method": "POST",
                "selection_modes": ["explicit"],
                "options": [],
                "preflight_endpoint": "/v1/universes/actions/activate/preflight",
            },
            {
                "id": "deactivate",
                "label": "Deactivate",
                "endpoint": "/v1/universes/actions/deactivate",
                "method": "POST",
                "tone": "danger",
                "selection_modes": ["explicit"],
                "options": [],
                "preflight_endpoint": "/v1/universes/actions/deactivate/preflight",
                "confirmation": {
                    "title": "Deactivate universes",
                    "word": "DEACTIVATE",
                    "button_label": "Deactivate",
                    "warning": "Inactive universes cannot be used for new market-data updates.",
                },
            },
            {
                "id": "remove",
                "label": "Delete",
                "endpoint": "/v1/universes/actions/remove",
                "method": "POST",
                "tone": "danger",
                "selection_modes": ["explicit"],
                "options": [],
                "preflight_endpoint": "/v1/universes/actions/remove/preflight",
                "confirmation": {
                    "title": "Delete universes",
                    "word": "DELETE",
                    "button_label": "Delete",
                    "warning": "The category and all of its memberships will be permanently deleted.",
                },
            }
        ],
    )


@router.post("/actions/run/preflight", response_model=dict[str, Any])
def universes_run_preflight(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    timeout = positive_float_option(request.options, "timeout", default=30.0, maximum=300.0)
    results: list[dict[str, Any]] = []
    blockers: list[str] = []
    for uid in request.selection.uids:
        universe = get_universe(uid)
        if universe is None:
            blockers.append(f"Missing universe: {uid}")
            continue
        if not universe.is_active:
            blockers.append(f"Universe {uid} is inactive.")
            continue
        if universe.source_uid is None:
            blockers.append(f"Universe {uid} has no configured holdings source.")
            continue
        try:
            preview = preview_universe_run(uid, timeout=timeout)
        except Exception:
            blockers.append(f"Universe {uid} could not be extracted and validated.")
            continue
        results.append(preview)
        if preview["has_blockers"]:
            blockers.append(f"Universe {uid} has unresolved asset-registration blockers.")
    return {
        "contract": "command-center.bulk_action_preflight@v1",
        "allowed": not blockers,
        "detail": (
            "Selected universes are ready to synchronize."
            if not blockers
            else "One or more universes are blocked from synchronization."
        ),
        "matched_count": len(results),
        "blockers": blockers,
        "warnings": [],
        "results": results,
    }


@router.post("/actions/run", response_model=dict[str, Any])
def universes_run(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    preflight = universes_run_preflight(request)
    if not preflight["allowed"]:
        raise HTTPException(status_code=409, detail=preflight)
    timeout = positive_float_option(request.options, "timeout", default=30.0, maximum=300.0)
    try:
        return {
            "results": [
                run_universe(uid, timeout=timeout) for uid in request.selection.uids
            ]
        }
    except Exception as exc:
        raise api_http_error(exc) from exc


def _universes_status_preflight(
    request: BulkActionRequest,
    *,
    is_active: bool,
) -> dict[str, Any]:
    universes = {uid: get_universe(uid) for uid in request.selection.uids}
    missing = [uid for uid, universe in universes.items() if universe is None]
    unchanged = [
        uid
        for uid, universe in universes.items()
        if universe is not None and universe.is_active is is_active
    ]
    state = "active" if is_active else "inactive"
    return {
        "contract": "command-center.bulk_action_preflight@v1",
        "allowed": not missing,
        "detail": (
            f"Selected universes will be marked {state}."
            if not missing
            else "One or more universes do not exist."
        ),
        "matched_count": len(request.selection.uids) - len(missing),
        "blockers": [f"Missing universe: {uid}" for uid in missing],
        "warnings": ([f"{len(unchanged)} selected universe(s) are already {state}."] if unchanged else []),
    }


@router.post("/actions/activate/preflight", response_model=dict[str, Any])
def universes_activate_preflight(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    try:
        return _universes_status_preflight(request, is_active=True)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/actions/activate", response_model=dict[str, Any])
def universes_activate(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    preflight = universes_activate_preflight(request)
    if not preflight["allowed"]:
        raise HTTPException(status_code=409, detail=preflight)
    try:
        return {
            "results": [
                update_universe(uid, MaterializedUniverseUpdateRequest(is_active=True))
                for uid in request.selection.uids
            ]
        }
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/actions/deactivate/preflight", response_model=dict[str, Any])
def universes_deactivate_preflight(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    try:
        return _universes_status_preflight(request, is_active=False)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/actions/deactivate", response_model=dict[str, Any])
def universes_deactivate(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    preflight = universes_deactivate_preflight(request)
    if not preflight["allowed"]:
        raise HTTPException(status_code=409, detail=preflight)
    try:
        return {
            "results": [
                update_universe(uid, MaterializedUniverseUpdateRequest(is_active=False))
                for uid in request.selection.uids
            ]
        }
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/actions/remove/preflight", response_model=dict[str, Any])
def universes_remove_preflight(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    try:
        universes = {uid: get_universe(uid) for uid in request.selection.uids}
        missing = [uid for uid, universe in universes.items() if universe is None]
        membership_count = sum(
            universe.asset_count for universe in universes.values() if universe is not None
        )
    except Exception as exc:
        raise api_http_error(exc) from exc
    return {
        "contract": "command-center.bulk_action_preflight@v1",
        "allowed": not missing,
        "detail": "Universe memberships and category rows will be deleted."
        if not missing
        else "One or more universes do not exist.",
        "matched_count": len(request.selection.uids) - len(missing),
        "blockers": [f"Missing universe: {uid}" for uid in missing],
        "warnings": [f"{membership_count} AssetCategory memberships will be deleted."],
    }


@router.post("/actions/remove", response_model=dict[str, Any])
def universes_remove(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    preflight = universes_remove_preflight(request)
    if not preflight["allowed"]:
        raise HTTPException(status_code=409, detail=preflight)
    try:
        return {"results": [delete_universe(uid) for uid in request.selection.uids]}
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{category_uid}", response_model=MaterializedUniverseResponse)
def universe_get(category_uid: str) -> MaterializedUniverseResponse:
    universe = get_universe(category_uid)
    if universe is None:
        raise not_found("Universe not found.")
    return universe


@router.patch("/{category_uid}", response_model=MaterializedUniverseResponse)
def universe_update(
    category_uid: str,
    request: MaterializedUniverseUpdateRequest = Body(...),
) -> MaterializedUniverseResponse:
    try:
        return update_universe(category_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/{category_uid}", response_model=dict[str, Any])
def universe_delete(category_uid: str) -> dict[str, Any]:
    try:
        return delete_universe(category_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc
