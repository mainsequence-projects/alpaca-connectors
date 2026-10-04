"""The caller of the current API request, as the SDK request identity verified it.

`create_app` installs `mainsequence.server.fastapi.install_request_identity` once. Before any route
runs it authenticates the request: hosted, through the platform-signed caller assertion; locally,
through a direct Bearer token checked against `MAINSEQUENCE_ENDPOINT`. It then binds that caller to
the request for `User.get_logged_user()`.

Routes read the caller only through this module. They never read `request.state`, an identity
header, or the process's own SDK user, and nothing replaces a caller the SDK did not verify.
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


def unauthenticated() -> HTTPException:
    return api_error(
        status_code=401,
        code="unauthenticated",
        message="The request has no authenticated caller.",
    )


__all__ = ["bound_caller_uid", "caller_uid"]
