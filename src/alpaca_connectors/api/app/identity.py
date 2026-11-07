"""The caller of the current API request, as the SDK request identity verified it.

`create_app` installs `mainsequence.server.fastapi.install_request_identity` once. Before any route
runs it authenticates the request: hosted, through the platform-signed caller assertion; locally,
through a direct Bearer token checked against `MAINSEQUENCE_ENDPOINT`. It then binds that caller to
the request for `User.get_logged_user()`, and attaches the request's W3C Baggage, the caller
context of tdag-django ADR-0057, to the request's OpenTelemetry context.

Routes read the caller only through this module. They never read `request.state`, an identity
header, the baggage, or the process's own SDK user, and nothing replaces a caller the SDK did not
verify.

The Alpaca Agent calls the API's MCP tools while it works for a person. On such a call the caller
is the Agent and the platform-signed requester is that person. Authorship follows one identity,
the acting user: the requester on a requester-bound call, the caller on any other, never both
combined.

Ownership follows the acting owner (ADR 0019): the acting user as `(uid, uid)`, except when a
caller on the vouching allow-list names an external person in the caller context, who is then
`(gateway, person id)`. Caller context from any other caller is logged and never used.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from msm.api.http import api_error
from opentelemetry import baggage

from alpaca_connectors.account.ownership import AccountOwner
from mainsequence.client import RequestIdentityError, User

from .caller_context import (
    CONTEXT_PREFIX,
    GATEWAY_KEY_NAME,
    MAX_GATEWAY_UID_LENGTH,
    MAX_USER_UID_LENGTH,
    USER_KEY_NAME,
    vouching_callers,
)

logger = logging.getLogger(__name__)


def bound_caller_uid() -> str | None:
    """Return the UID of the request's verified caller, or None when no caller is bound.

    No caller is bound outside a request, and for an `OPTIONS` request, which the SDK admits
    without one. The deployment declares no public ingress, so every other request has a caller.
    """
    try:
        return User.get_logged_user().uid
    except RequestIdentityError:
        return None


def caller_uid() -> str:
    """Return the UID of the request's verified caller, or reject the request with 401."""
    uid = bound_caller_uid()
    if uid is None:
        raise unauthenticated()
    return uid


def requester_uid() -> str | None:
    """Return the UID of the person a requester-bound call works for, or None."""
    try:
        requester = User.get_requester()
    except RequestIdentityError:
        return None
    return requester.uid if requester is not None else None


def bound_acting_user_uid() -> str | None:
    """Return the acting user's UID, or None when no caller is bound."""
    return requester_uid() or bound_caller_uid()


def acting_user_uid() -> str:
    """Return the acting user's UID, or reject the request with 401."""
    uid = bound_acting_user_uid()
    if uid is None:
        raise unauthenticated()
    return uid


def _context_value(value: object, *, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_length
        and value.strip() == value
        and value.isprintable()
    )


def _names_an_external_person() -> bool:
    return any(
        key.startswith(CONTEXT_PREFIX)
        and key.endswith((f".{GATEWAY_KEY_NAME}", f".{USER_KEY_NAME}"))
        for key in baggage.get_all()
    )


def acting_owner() -> AccountOwner:
    """Return who an account registered or reached by this request belongs to (ADR 0019).

    - A requester-bound call acts for its requester; the caller context is ignored.
    - A caller on the vouching allow-list that names a person with both keys, through a gateway
      its entry allows, acts for that external person.
    - Such a caller naming a person through another gateway is refused with 403; one key only,
      an empty, oversized or non-printable value, or equal values are refused with 400. A refusal
      never falls back to the caller's own identity.
    - Any other call acts for its caller, as before; caller context it carries is logged as an
      unverified claim and never used.
    """
    requester = requester_uid()
    if requester is not None:
        return AccountOwner.main_sequence_user(requester)
    caller = caller_uid()
    entry = vouching_callers().get(caller)
    if entry is None:
        if _names_an_external_person():
            logger.info(
                "Ignored caller context from a caller that may not vouch for a person.",
                extra={"caller_uid": caller},
            )
        return AccountOwner.main_sequence_user(caller)
    gateway_uid = baggage.get_baggage(entry.gateway_key)
    user_uid = baggage.get_baggage(entry.user_key)
    if gateway_uid is None and user_uid is None:
        return AccountOwner.main_sequence_user(caller)
    if (
        not _context_value(gateway_uid, max_length=MAX_GATEWAY_UID_LENGTH)
        or not _context_value(user_uid, max_length=MAX_USER_UID_LENGTH)
        or gateway_uid == user_uid
    ):
        raise caller_context_invalid()
    if gateway_uid not in entry.gateway_uids:
        raise api_error(
            status_code=403,
            code="caller_context_untrusted_gateway",
            message="The caller may not vouch for people of this gateway.",
        )
    return AccountOwner(gateway_uid=str(gateway_uid), user_uid=str(user_uid))


def caller_context_invalid(
    message: str = "The caller context must name one person with both of its keys.",
) -> HTTPException:
    return api_error(status_code=400, code="caller_context_invalid", message=message)


def unauthenticated() -> HTTPException:
    return api_error(
        status_code=401,
        code="unauthenticated",
        message="The request has no authenticated caller.",
    )


__all__ = [
    "acting_owner",
    "acting_user_uid",
    "bound_acting_user_uid",
    "bound_caller_uid",
    "caller_context_invalid",
    "caller_uid",
    "requester_uid",
]
