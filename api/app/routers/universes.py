"""Registered Asset Universe API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from msm.api.http import BulkActionPreflightResponse

from ..errors import api_http_error, not_found
from ..schemas import (
    AssetUniverseCreateRequest,
    AssetUniverseResponse,
    AssetUniverseUpdateRequest,
    BulkActionRequest,
    ResourceCollection,
    ResourceDiscoveryResponse,
)
from ..services.common import (
    positive_float_option,
    required_string_option,
    resource_discovery,
    validate_ordering,
    validate_page_window,
)
from ..services.universes import (
    create_universe,
    delete_universe,
    get_universe,
    list_universe_assets,
    list_universes,
    preview_universe_run,
    run_universe,
    universe_delete_blockers,
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
    validate_ordering(ordering, allowed_fields={"display_name", "symbol", "updated_at"})
    try:
        return list_universes(limit=limit, offset=offset, search=search, ordering=ordering)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("", response_model=AssetUniverseResponse, status_code=201)
def universe_create(
    request: AssetUniverseCreateRequest = Body(...),
) -> AssetUniverseResponse:
    try:
        return create_universe(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def universes_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="asset-universes",
        label="Registered Universes",
        item_label="universe",
        identity_fields=["uid"],
        searchable_fields=["display_name", "symbol"],
        orderable_fields=["display_name", "symbol", "updated_at"],
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
            {"id": "symbol", "header": "Symbol", "sortable_key": "symbol"},
            {"id": "asset_count", "header": "Assets", "data_type": "number"},
            {"id": "is_active", "header": "Status", "data_type": "boolean"},
        ],
        actions=[
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
            },
        ],
    )


@router.post("/actions/run/preflight", response_model=BulkActionPreflightResponse)
def universes_run_preflight(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    timeout = positive_float_option(request.options, "timeout", default=30.0, maximum=300.0)
    account_uid = required_string_option(request.options, "account_uid")
    results: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for uid in request.selection.uids:
        universe = get_universe(uid)
        if universe is None:
            blockers.append(f"Missing universe: {uid}")
            continue
        if not universe.is_active:
            blockers.append(f"Universe {uid} is inactive.")
            continue
        try:
            preview = preview_universe_run(uid, account_uid=account_uid, timeout=timeout)
        except (LookupError, ValueError) as exc:
            blockers.append(str(exc))
            continue
        except Exception:
            blockers.append(f"Universe {uid} could not prepare component extraction.")
            continue
        results.append(preview)
        warnings.extend(preview.get("warnings", []))
        if preview["has_blockers"]:
            blockers.extend(
                preview.get("blockers")
                or [f"Universe {uid} has unresolved asset-registration blockers."]
            )
    return {
        "contract": "command-center.bulk_action_preflight@v1",
        "allowed": not blockers,
        "detail": (
            "Selected universes are ready to extract components. Missing constituents will be "
            "registered automatically through the selected Alpaca account. Market-data bars "
            "will not be updated."
            if not blockers
            else "One or more universes cannot extract components."
        ),
        "matched_count": len(results),
        "blockers": blockers,
        "warnings": warnings,
        "results": results,
    }


@router.post("/actions/run", response_model=dict[str, Any])
def universes_run(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    timeout = positive_float_option(request.options, "timeout", default=30.0, maximum=300.0)
    account_uid = required_string_option(request.options, "account_uid")
    try:
        return {
            "results": [
                run_universe(uid, account_uid=account_uid, timeout=timeout)
                for uid in request.selection.uids
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
        "warnings": (
            [f"{len(unchanged)} selected universe(s) are already {state}."] if unchanged else []
        ),
    }


@router.post("/actions/activate/preflight", response_model=BulkActionPreflightResponse)
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
                update_universe(uid, AssetUniverseUpdateRequest(is_active=True))
                for uid in request.selection.uids
            ]
        }
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/actions/deactivate/preflight", response_model=BulkActionPreflightResponse)
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
                update_universe(uid, AssetUniverseUpdateRequest(is_active=False))
                for uid in request.selection.uids
            ]
        }
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/actions/remove/preflight", response_model=BulkActionPreflightResponse)
def universes_remove_preflight(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    try:
        universes = {uid: get_universe(uid) for uid in request.selection.uids}
        missing = [uid for uid, universe in universes.items() if universe is None]
        dependency_blockers = [
            blocker
            for uid, universe in universes.items()
            if universe is not None
            for blocker in universe_delete_blockers(uid)
        ]
        membership_count = sum(
            universe.asset_count for universe in universes.values() if universe is not None
        )
    except Exception as exc:
        raise api_http_error(exc) from exc
    return {
        "contract": "command-center.bulk_action_preflight@v1",
        "allowed": not missing and not dependency_blockers,
        "detail": (
            "The Asset Universe, its memberships, and its category will be deleted."
            if not missing and not dependency_blockers
            else (
                "One or more universes do not exist."
                if missing
                else "One or more universes have blocking dependent configurations."
            )
        ),
        "matched_count": len(request.selection.uids) - len(missing),
        "blockers": [f"Missing universe: {uid}" for uid in missing] + dependency_blockers,
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


@router.get("/{universe_uid}/assets", response_model=ResourceCollection)
def universe_assets_list(
    universe_uid: str,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    ordering: str = "ticker",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"ticker", "alpaca_asset_id"})
    try:
        return list_universe_assets(
            universe_uid,
            limit=limit,
            offset=offset,
            search=search,
            ordering=ordering,
        )
    except LookupError as exc:
        raise not_found(str(exc)) from exc
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{universe_uid}", response_model=AssetUniverseResponse)
def universe_get(universe_uid: str) -> AssetUniverseResponse:
    universe = get_universe(universe_uid)
    if universe is None:
        raise not_found("Universe not found.")
    return universe


@router.patch("/{universe_uid}", response_model=AssetUniverseResponse)
def universe_update(
    universe_uid: str,
    request: AssetUniverseUpdateRequest = Body(...),
) -> AssetUniverseResponse:
    try:
        return update_universe(universe_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/{universe_uid}", response_model=dict[str, Any])
def universe_delete(universe_uid: str) -> dict[str, Any]:
    try:
        return delete_universe(universe_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc
