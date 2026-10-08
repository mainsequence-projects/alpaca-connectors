"""The signed-in person's account key requests, as the Alpaca site reads them (ADR 0015).

The Agent creates requests through the MCP tool `alpaca_account_key_request`. These routes only
list, read, and cancel the acting user's own requests; the keys themselves are submitted with
`POST /v1/accounts` or `PATCH /v1/accounts/{account_uid}` and the request's `key_request_uid`.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from ..errors import api_http_error
from ..identity import acting_user_uid
from ..schemas import AccountKeyRequestCollectionResponse, AccountKeyRequestResponse
from ..services.account_key_requests import (
    cancel_own_key_request,
    list_own_pending_key_requests,
    own_key_request,
)

# Mounted before the accounts router, whose `/{account_uid}` routes would otherwise match.
router = APIRouter(prefix="/v1/accounts/key-requests", tags=["Accounts"])


@router.get("", response_model=AccountKeyRequestCollectionResponse)
def account_key_requests_list(response: Response) -> AccountKeyRequestCollectionResponse:
    """The acting user's pending, unexpired key requests, newest first."""
    response.headers["Cache-Control"] = "no-store"
    requester_uid = acting_user_uid()
    try:
        return list_own_pending_key_requests(requester_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{key_request_uid}", response_model=AccountKeyRequestResponse)
def account_key_request_get(key_request_uid: str, response: Response) -> AccountKeyRequestResponse:
    response.headers["Cache-Control"] = "no-store"
    requester_uid = acting_user_uid()
    try:
        return own_key_request(key_request_uid, requester_uid=requester_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post("/{key_request_uid}/actions/cancel", response_model=AccountKeyRequestResponse)
def account_key_request_cancel(key_request_uid: str) -> AccountKeyRequestResponse:
    requester_uid = acting_user_uid()
    try:
        return cancel_own_key_request(key_request_uid, requester_uid=requester_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc
