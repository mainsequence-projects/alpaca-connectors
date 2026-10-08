"""API shaping for account key requests (ADR 0015).

The Agent starts a request for the person it works for; the person enters the keys on the Alpaca
site, which lists and submits only that person's own requests.
"""

from __future__ import annotations

import os
from typing import Any

from alpaca_connectors.account import environment_for_is_paper
from alpaca_connectors.account.key_requests import (
    AccountKeyRequest,
    cancel_account_key_request,
    create_account_key_request,
    describe_account_key_request,
    get_own_account_key_request,
    list_pending_account_key_requests,
)

from ..schemas import AccountKeyRequestCollectionResponse, AccountKeyRequestResponse

# Where the person enters the keys: the Alpaca site's Command Center link. A plain, non-secret
# deployment setting; without it the Agent describes the site instead of linking to it.
SITE_URL_ENV = "ALPACA_CONNECTORS_SITE_URL"


def site_url() -> str | None:
    value = os.getenv(SITE_URL_ENV, "").strip()
    return value or None


def key_request_response(request: AccountKeyRequest) -> AccountKeyRequestResponse:
    return AccountKeyRequestResponse.model_validate(describe_account_key_request(request))


def _started(request: AccountKeyRequest) -> dict[str, Any]:
    environment = request.environment
    task = (
        "register the new account"
        if request.action == "register"
        else f"replace the keys of account {request.account_uid}"
    )
    link = site_url()
    where = f"the Alpaca Connectors site at {link}" if link else "the Alpaca Connectors site"
    return {
        "key_request": key_request_response(request).model_dump(mode="json"),
        "site_url": link,
        "instructions": (
            f"Ask the person to open {where}, go to Accounts, and enter the Alpaca {environment} "
            f"API key and secret key for the pending request to {task}. The site sends the keys "
            "only to the API, which stores them as Main Sequence Secrets. Never ask for or accept "
            "the keys in this conversation. Check progress with the status operation; the "
            f"request expires at {request.expires_at.isoformat()}."
        ),
    }


def start_registration_key_request(
    *,
    requester_uid: str,
    environment: str,
    account_name: str | None,
) -> dict[str, Any]:
    request = create_account_key_request(
        requester_uid=requester_uid,
        action="register",
        environment=environment,
        account_name=account_name,
    )
    return _started(request)


def start_rotation_key_request(*, requester_uid: str, account_uid: str) -> dict[str, Any]:
    from .accounts import get_account

    account = get_account(account_uid)
    if account is None:
        raise LookupError(f"Account {account_uid} does not exist.")
    request = create_account_key_request(
        requester_uid=requester_uid,
        action="rotate",
        environment=environment_for_is_paper(account.is_paper),
        account_uid=account_uid,
    )
    return _started(request)


def own_key_request(request_uid: str, *, requester_uid: str) -> AccountKeyRequestResponse:
    return key_request_response(
        get_own_account_key_request(request_uid, requester_uid=requester_uid)
    )


def list_own_pending_key_requests(requester_uid: str) -> AccountKeyRequestCollectionResponse:
    return AccountKeyRequestCollectionResponse(
        items=[
            key_request_response(request)
            for request in list_pending_account_key_requests(requester_uid)
        ]
    )


def cancel_own_key_request(request_uid: str, *, requester_uid: str) -> AccountKeyRequestResponse:
    return key_request_response(
        cancel_account_key_request(request_uid, requester_uid=requester_uid)
    )


__all__ = [
    "SITE_URL_ENV",
    "cancel_own_key_request",
    "key_request_response",
    "list_own_pending_key_requests",
    "own_key_request",
    "site_url",
    "start_registration_key_request",
    "start_rotation_key_request",
]
