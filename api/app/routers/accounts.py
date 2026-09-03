"""Registered Alpaca account API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from ..errors import api_http_error, not_found
from ..schemas import (
    AccountRegistrationRequest,
    AccountResponse,
    AccountUpdateRequest,
    BulkActionRequest,
    ResourceCollection,
    ResourceDiscoveryResponse,
    SecretReferenceCollectionResponse,
)
from ..services.accounts import (
    create_account_registration,
    get_account,
    list_accounts,
    list_secret_references,
    preflight_account_registration,
    refresh_account,
    remove_account,
    update_account,
)
from ..services.common import (
    boolean_option,
    resource_discovery,
    validate_ordering,
    validate_page_window,
)
from ..services.holdings import capture_holdings, preflight_holdings

router = APIRouter(prefix="/v1/accounts", tags=["Accounts"])


@router.get("", response_model=ResourceCollection)
def accounts_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    active: bool | None = None,
    is_paper: bool | None = None,
    ordering: str = "account_name",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"account_name", "unique_identifier"})
    try:
        return list_accounts(
            limit=limit,
            offset=offset,
            search=search,
            active=active,
            is_paper=is_paper,
            ordering=ordering,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/secret-references", response_model=SecretReferenceCollectionResponse)
def account_secret_references_list(
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
) -> SecretReferenceCollectionResponse:
    """List Secret names available to this runtime without exposing values."""
    validate_page_window(limit=limit, offset=offset)
    try:
        return SecretReferenceCollectionResponse.model_validate(
            list_secret_references(
                limit=limit,
                offset=offset,
                search=search,
            ).model_dump(mode="json")
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def accounts_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="alpaca-accounts",
        label="Alpaca Accounts",
        item_label="account",
        identity_fields=["uid"],
        searchable_fields=["account_name", "unique_identifier"],
        filterable_fields=["account_is_active", "is_paper"],
        filter_types={"account_is_active": "boolean", "is_paper": "boolean"},
        orderable_fields=["account_name", "unique_identifier"],
        columns=[
            {
                "id": "account_name",
                "header": "Account",
                "sortable_key": "account_name",
                "hideable": False,
            },
            {"id": "unique_identifier", "header": "Identifier"},
            {"id": "is_paper", "header": "Paper", "data_type": "boolean"},
            {"id": "account_is_active", "header": "Active", "data_type": "boolean"},
            {"id": "snapshot_time", "header": "Last Refresh", "data_type": "datetime"},
        ],
        actions=[
            {
                "id": "capture-holdings",
                "label": "Capture holdings",
                "endpoint": "/actions/capture-holdings",
                "method": "POST",
                "selection_modes": ["explicit"],
                "options": [
                    {
                        "key": "register_missing_assets",
                        "type": "boolean",
                        "default": True,
                        "label": "Register missing assets",
                        "description": "Register strictly FIGI-resolved held equities.",
                    }
                ],
                "preflight_endpoint": "/actions/capture-holdings/preflight",
            },
            {
                "id": "remove",
                "label": "Remove registration",
                "endpoint": "/actions/remove",
                "method": "POST",
                "tone": "danger",
                "selection_modes": ["explicit"],
                "options": [],
                "preflight_endpoint": "/actions/remove/preflight",
            },
        ],
    )


@router.post("/registration/preflight", response_model=dict[str, Any])
def account_registration_preflight(
    request: AccountRegistrationRequest = Body(...),
) -> dict[str, Any]:
    try:
        return preflight_account_registration(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("", response_model=AccountResponse, status_code=201)
def account_create(request: AccountRegistrationRequest = Body(...)) -> AccountResponse:
    try:
        return create_account_registration(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/actions/remove/preflight", response_model=dict[str, Any])
def accounts_remove_preflight(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    try:
        missing = [uid for uid in request.selection.uids if get_account(uid) is None]
        retained_rows = 0
        if not missing:
            from src.holdings import list_account_holdings

            for uid in request.selection.uids:
                _, count = list_account_holdings(uid, limit=1)
                retained_rows += count
    except Exception as exc:
        raise api_http_error(exc) from exc
    return {
        "contract": "command-center.bulk_action_preflight@v1",
        "allowed": not missing,
        "detail": (
            "Registrations will be removed, accounts deactivated, and holdings retained."
            if not missing
            else "One or more account registrations do not exist."
        ),
        "matched_count": len(request.selection.uids) - len(missing),
        "blockers": [f"Missing account: {uid}" for uid in missing],
        "warnings": [f"{retained_rows} historical holdings rows will be retained."],
    }


@router.post("/actions/remove", response_model=dict[str, Any])
def accounts_remove(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    preflight = accounts_remove_preflight(request)
    if not preflight["allowed"]:
        raise HTTPException(status_code=409, detail=preflight)
    try:
        return {"results": [remove_account(uid) for uid in request.selection.uids]}
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/actions/capture-holdings/preflight", response_model=dict[str, Any])
def accounts_capture_holdings_preflight(
    request: BulkActionRequest = Body(...),
) -> dict[str, Any]:
    results = []
    blockers = []
    for uid in request.selection.uids:
        try:
            results.append(preflight_holdings(uid))
        except Exception:
            blockers.append(f"Account {uid} is not ready for holdings capture.")
    return {
        "contract": "command-center.bulk_action_preflight@v1",
        "allowed": not blockers,
        "detail": (
            "Selected accounts are ready for holdings capture."
            if not blockers
            else "One or more accounts are not ready for holdings capture."
        ),
        "matched_count": len(results),
        "blockers": blockers,
        "warnings": [warning for result in results for warning in result.get("warnings", [])],
        "results": results,
    }


@router.post("/actions/capture-holdings", response_model=dict[str, Any])
def accounts_capture_holdings(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    register_missing = boolean_option(
        request.options,
        "register_missing_assets",
        default=True,
    )
    preflight = accounts_capture_holdings_preflight(request)
    if not preflight["allowed"]:
        raise HTTPException(status_code=409, detail=preflight)
    try:
        return {
            "results": [
                capture_holdings(uid, register_missing_assets=register_missing)
                for uid in request.selection.uids
            ]
        }
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{account_uid}", response_model=AccountResponse)
def account_get(account_uid: str) -> AccountResponse:
    account = get_account(account_uid)
    if account is None:
        raise not_found("Account registration not found.")
    return account


@router.patch("/{account_uid}", response_model=AccountResponse)
def account_update(
    account_uid: str,
    request: AccountUpdateRequest = Body(...),
) -> AccountResponse:
    try:
        return update_account(account_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/{account_uid}", response_model=dict[str, Any])
def account_delete(account_uid: str) -> dict[str, Any]:
    try:
        return remove_account(account_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/{account_uid}/actions/refresh", response_model=AccountResponse)
def account_refresh(account_uid: str) -> AccountResponse:
    try:
        return refresh_account(account_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc
