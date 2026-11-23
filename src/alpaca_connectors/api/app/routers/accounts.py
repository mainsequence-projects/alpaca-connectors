"""Registered Alpaca account API.

Every account is owned by a person pair (ADR 0019). A call for an external person, vouched for by
a caller on the allow-list, registers accounts for that person and reaches only that person's
accounts; every other call is unchanged.

`POST /v1/accounts/{account_uid}/actions/sync` is the account sync every venue connector serves
(issue #14): it answers 200 with a terminal `ObservableOperation`, which
`GET /v1/operations/{operation_uid}` answers again to its owner.

Removing keys and deleting an account are separate actions (ADR 0028, decision 8), both for the
account's owner only: `actions/remove-keys` clears the keys and keeps everything else;
`DELETE /v1/accounts/{account_uid}` deletes the account and everything that belongs to it, and
takes the exact confirmation `DELETE <account uid>`.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from msm.api.http import BulkActionPreflightResponse

from alpaca_connectors.account.ownership import AccountOwner

from ..errors import api_http_error, bad_request, not_found
from ..identity import acting_owner, acting_user_uid, caller_context_invalid
from ..schemas import (
    AccountRegistrationRequest,
    AccountResponse,
    AccountSyncOperationResponse,
    AccountUpdateRequest,
    BulkActionRequest,
    ResourceCollection,
    ResourceDiscoveryResponse,
)
from ..services.account_sync import sync_account
from ..services.accounts import (
    accessible_account_registrations,
    create_account_registration,
    delete_account,
    get_account,
    get_account_registration,
    list_accounts,
    log_account_action,
    preflight_account_registration,
    preflight_delete,
    refresh_account,
    remove_keys,
    remove_keys_of_accounts,
    require_account_access,
    require_accounts_access,
    require_accounts_owned_by,
    update_account,
)
from ..services.common import (
    collection_response,
    resource_discovery,
    validate_ordering,
    validate_page_window,
)
from ..services.holdings import capture_holdings, preflight_holdings

router = APIRouter(prefix="/v1/accounts", tags=["Accounts"])

KEY_REQUEST_NOT_FOR_EXTERNAL = (
    "Account key requests belong to Main Sequence people signed in to the site; an external "
    "person's keys are submitted in the request."
)


@router.get("", response_model=ResourceCollection)
def accounts_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    active: bool | None = None,
    is_paper: bool | None = None,
    ordering: str = "account_name",
    owner_gateway_uid: str | None = Query(default=None, min_length=1, max_length=64),
    owner_user_uid: str | None = Query(default=None, min_length=1, max_length=255),
    uid: list[uuid.UUID] | None = Query(
        default=None, description="Only these accounts; repeat the parameter for each."
    ),
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"account_name", "unique_identifier"})
    if (owner_gateway_uid is None) != (owner_user_uid is None):
        raise bad_request("Filter by owner with both owner_gateway_uid and owner_user_uid.")
    owner = (
        AccountOwner(gateway_uid=owner_gateway_uid, user_uid=owner_user_uid)
        if owner_gateway_uid is not None and owner_user_uid is not None
        else None
    )
    acting = acting_owner()
    if acting.is_external:
        # An external person lists only their own accounts; a filter can only narrow that.
        if owner is not None and owner != acting:
            return collection_response(items=[], total=0, limit=limit, offset=offset)
        owner = acting
    try:
        return list_accounts(
            limit=limit,
            offset=offset,
            search=search,
            active=active,
            is_paper=is_paper,
            ordering=ordering,
            owner=owner,
            uids=[str(value) for value in uid] if uid is not None else None,
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
        filterable_fields=["account_is_active", "is_paper", "owner_gateway_uid", "owner_user_uid"],
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
            # False once the owner removed the keys (`credentials_removed`, ADR 0028).
            {"id": "credentials_stored", "header": "Keys", "data_type": "boolean"},
            {"id": "snapshot_time", "header": "Last Refresh", "data_type": "datetime"},
        ],
        actions=[
            {
                "id": "capture-holdings",
                "label": "Capture holdings",
                "endpoint": f"{router.prefix}/actions/capture-holdings",
                "method": "POST",
                "selection_modes": ["explicit"],
                "options": [],
                "preflight_endpoint": f"{router.prefix}/actions/capture-holdings/preflight",
            },
            {
                "id": "remove-keys",
                "label": "Remove keys",
                "endpoint": f"{router.prefix}/actions/remove-keys",
                "method": "POST",
                "tone": "danger",
                "selection_modes": ["explicit"],
                "options": [],
                "preflight_endpoint": f"{router.prefix}/actions/remove-keys/preflight",
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
def account_create(
    request: AccountRegistrationRequest = Body(...),
) -> AccountResponse:
    # The acting user is recorded as the credential author (ADR 0011, ADR 0014, ADR 0015), and
    # the acting owner as the account's owner (ADR 0019).
    actor_user_uid = acting_user_uid()
    owner = acting_owner()
    if owner.is_external and request.key_request_uid is not None:
        raise caller_context_invalid(KEY_REQUEST_NOT_FOR_EXTERNAL)
    try:
        account = create_account_registration(request, actor_user_uid=actor_user_uid, owner=owner)
    except Exception as exc:
        raise api_http_error(exc) from exc
    registered_uid = account.get("uid") if isinstance(account, dict) else account.uid
    log_account_action("registered", owner=owner, account_uid=registered_uid)
    return account


@router.post("/actions/remove-keys/preflight", response_model=BulkActionPreflightResponse)
def accounts_remove_keys_preflight(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    """Removing keys clears them and keeps everything else; only each account's owner may."""
    owner = acting_owner()
    try:
        missing = require_accounts_owned_by(request.selection.uids, owner)
    except Exception as exc:
        raise api_http_error(exc) from exc
    blockers = [f"Missing account: {uid}" for uid in missing]
    return {
        "contract": "command-center.bulk_action_preflight@v1",
        "allowed": not blockers,
        "detail": (
            "The stored keys will be removed. The owners, the accounts, their history, portfolios "
            "and configurations stay; runs fail until keys are registered again."
            if not blockers
            else "One or more selected accounts do not exist."
        ),
        "matched_count": len(request.selection.uids) - len(missing),
        "blockers": blockers,
        "warnings": [],
    }


@router.post("/actions/remove-keys", response_model=dict[str, Any])
def accounts_remove_keys(request: BulkActionRequest = Body(...)) -> dict[str, Any]:
    preflight = accounts_remove_keys_preflight(request)
    if not preflight["allowed"]:
        raise HTTPException(status_code=409, detail=preflight)
    owner = acting_owner()
    try:
        results = remove_keys_of_accounts(request.selection.uids, owner=owner)
    except Exception as exc:
        raise api_http_error(exc) from exc
    for uid in request.selection.uids:
        log_account_action("keys_removed", owner=owner, account_uid=uid)
    return {"results": results}


@router.post(
    "/actions/capture-holdings/preflight",
    response_model=BulkActionPreflightResponse,
)
def accounts_capture_holdings_preflight(
    request: BulkActionRequest = Body(...),
) -> dict[str, Any]:
    if request.options:
        raise bad_request(
            "Capture holdings accepts no options; complete asset registration is mandatory."
        )
    registrations = accessible_account_registrations(request.selection.uids, acting_owner())
    return _capture_holdings_preflight(request, registrations)


def _capture_holdings_preflight(
    request: BulkActionRequest, registrations: dict[str, dict]
) -> dict[str, Any]:
    """Each selected account's capture plan. Alpaca is read once per account: each has its own
    keys. Their registrations were read once for all of them."""
    results = []
    blockers = []
    for uid in request.selection.uids:
        try:
            if uid not in registrations:
                raise LookupError(uid)
            result = preflight_holdings(uid, registration=registrations[uid])
            results.append(result)
            if not result.get("allowed", False):
                blockers.extend(result.get("blockers") or [f"Account {uid} is not ready."])
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
    if request.options:
        raise bad_request(
            "Capture holdings accepts no options; complete asset registration is mandatory."
        )
    owner = acting_owner()
    registrations = accessible_account_registrations(request.selection.uids, owner)
    preflight = _capture_holdings_preflight(request, registrations)
    if not preflight["allowed"]:
        raise HTTPException(status_code=409, detail=preflight)
    try:
        results = [
            capture_holdings(uid, registration=registrations[uid]) for uid in request.selection.uids
        ]
    except Exception as exc:
        raise api_http_error(exc) from exc
    for uid in request.selection.uids:
        log_account_action("holdings captured", owner=owner, account_uid=uid)
    return {"results": results}


@router.get("/{account_uid}", response_model=AccountResponse)
def account_get(account_uid: str) -> AccountResponse:
    require_account_access(account_uid, acting_owner())
    account = get_account(account_uid)
    if account is None:
        raise not_found("Account registration not found.")
    return account


@router.patch("/{account_uid}", response_model=AccountResponse)
def account_update(
    account_uid: str,
    request: AccountUpdateRequest = Body(...),
) -> AccountResponse:
    actor_user_uid = acting_user_uid()
    owner = acting_owner()
    require_account_access(account_uid, owner)
    if owner.is_external and request.key_request_uid is not None:
        raise caller_context_invalid(KEY_REQUEST_NOT_FOR_EXTERNAL)
    try:
        account = update_account(account_uid, request, actor_user_uid=actor_user_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc
    if request.credentials is not None:
        log_account_action("keys rotated", owner=owner, account_uid=account_uid)
    return account


@router.post("/{account_uid}/actions/remove-keys", response_model=dict[str, Any])
def account_remove_keys(account_uid: str) -> dict[str, Any]:
    """Clear the account's keys; the owner, the account and everything that belongs to it stay."""
    owner = acting_owner()
    require_account_access(account_uid, owner)
    try:
        result = remove_keys(account_uid, owner=owner)
    except Exception as exc:
        raise api_http_error(exc) from exc
    log_account_action("keys_removed", owner=owner, account_uid=account_uid)
    return result


@router.post("/{account_uid}/actions/delete/preflight", response_model=dict[str, Any])
def account_delete_preflight(account_uid: str) -> dict[str, Any]:
    """What deleting the account would delete, and what refuses it; nothing changes."""
    owner = acting_owner()
    require_account_access(account_uid, owner)
    try:
        return preflight_delete(account_uid, owner=owner)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/{account_uid}", response_model=dict[str, Any])
def account_delete(
    account_uid: str,
    confirm: str = Query(
        description="Exactly `DELETE <account uid>`: the account and all it owns are deleted."
    ),
) -> dict[str, Any]:
    """Delete the account and everything that belongs to it (ADR 0028, decision 8).

    Its portfolios, bars feeds, signal definitions and their signals, and its whole Markets
    history go. Refused, with nothing changed, while another account currently holds one of
    its portfolios.
    """
    if confirm != f"DELETE {account_uid}":
        raise bad_request("Confirm with exactly `DELETE <account uid>`.")
    owner = acting_owner()
    require_account_access(account_uid, owner)
    try:
        result = delete_account(account_uid, owner=owner)
    except Exception as exc:
        raise api_http_error(exc) from exc
    log_account_action("deleted", owner=owner, account_uid=account_uid)
    return result


@router.post("/{account_uid}/actions/refresh", response_model=AccountResponse)
def account_refresh(account_uid: str) -> AccountResponse:
    require_account_access(account_uid, acting_owner())
    try:
        return refresh_account(account_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/{account_uid}/actions/sync",
    response_model=AccountSyncOperationResponse,
    summary="Sync an account's holdings into ms-markets",
    description=(
        "Read the Alpaca account with its stored keys and publish one AccountHoldingsSet into "
        "ms-markets. When the account has a target there, size each exposure into units with "
        "Alpaca's equity, the portfolio weights and the latest trades, and allocate the snapshot "
        "to the account's funds; ms-markets records each owner's target, assigned units and "
        "delta. Then answer the terminal operation: succeeded with the snapshot and its lines, "
        "or failed at a step with a public error. Read the snapshot from ms-markets; read the "
        "operation again at GET /v1/operations/{operation_uid}. An unknown account, or one the "
        "acting owner may not reach (ADR 0019), is refused with 404 and runs nothing."
    ),
)
def account_sync(account_uid: str) -> AccountSyncOperationResponse:
    owner = acting_owner()
    require_account_access(account_uid, owner)
    try:
        registration = get_account_registration(account_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc
    if registration is None:
        raise not_found("Account registration not found.")
    try:
        # The registration read for the 404 serves the sync (issue #46).
        operation = sync_account(account_uid, owner=owner, registration=registration)
    except Exception as exc:
        raise api_http_error(exc) from exc
    log_account_action(
        "synced" if operation.status == "succeeded" else "sync failed",
        owner=owner,
        account_uid=account_uid,
        error_code=operation.error.code if operation.error else None,
    )
    return operation
