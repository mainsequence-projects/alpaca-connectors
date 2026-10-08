"""The caller of the current API request, as the SDK request identity verified it.

`create_app` installs `mainsequence.server.fastapi.install_request_identity` once. Before any route
runs it authenticates the request: hosted, through the platform-signed caller assertion; locally,
through a direct Bearer token checked against `MAINSEQUENCE_ENDPOINT`. It then binds that caller to
the request for `User.get_logged_user()`.

Routes read the caller only through this module. They never read `request.state`, an identity
header, or the process's own SDK user, and nothing replaces a caller the SDK did not verify.

The Alpaca Agent calls the API's MCP tools while it works for a person. On such a call the caller
is the Agent and the platform-signed requester is that person. Authorship and ownership follow one
identity, the acting user: the requester on a requester-bound call, the caller on any other, never
both combined.
"""

from __future__ import annotations

from fastapi import HTTPException
from msm.api.http import api_error

from mainsequence.client import RequestIdentityError, User


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


def unauthenticated() -> HTTPException:
    return api_error(
        status_code=401,
        code="unauthenticated",
        message="The request has no authenticated caller.",
    )


__all__ = [
    "acting_user_uid",
    "bound_acting_user_uid",
    "bound_caller_uid",
    "caller_uid",
    "requester_uid",
]
