"""Stable, user-safe HTTP error construction."""

from __future__ import annotations

import logging

from fastapi import HTTPException
from msm.api.http import api_error
from msm.api.http import api_http_error as markets_api_http_error
from msm.api.http import not_found as not_found
from pydantic import ValidationError

from alpaca_connectors.account.credentials import (
    AccountCredentialsMissingError,
    AlpacaCredentialsRejectedError,
    StoredCredentialError,
)
from alpaca_connectors.account.ownership import AccountInUseError, AccountOwnedByAnotherError
from alpaca_connectors.dependencies import STARTING_RETRY_SECONDS, is_dependency_starting
from alpaca_connectors.execution.orders import PreTradeCheckError
from alpaca_connectors.platform_secrets import PlatformSecretAccessError
from metatables import EncryptionError

logger = logging.getLogger(__name__)

DEPENDENCY_STARTING = "A service this operation needs is starting. Try again in a few seconds."
PROVIDER_PAYLOAD_UNREADABLE = "A provider answered with data that could not be read."


def bad_request(message: str) -> HTTPException:
    return api_error(status_code=400, code="invalid_request", message=message)


def dependency_starting() -> HTTPException:
    """``503 dependency_starting``: a release the work needs is starting (ADR 0020)."""
    error = api_error(
        status_code=503,
        code="dependency_starting",
        message=DEPENDENCY_STARTING,
        retryable=True,
    )
    error.headers = {"Retry-After": str(STARTING_RETRY_SECONDS)}
    return error


def _identifier(value: object) -> str | None:
    """A short error code as given, or None; never free text that can quote submitted values."""
    if isinstance(value, str) and 0 < len(value) <= 80 and value.replace("_", "").isalnum():
        return value
    return None


def upstream_failure(exc: BaseException) -> dict[str, object]:
    """What a refused Main Sequence call answered, for the log (issue #27).

    The status, method, URL path and public error code of the first SDK ``ApiError`` in the
    exception's chain. Never the answer's text or the request's values, which can quote the data
    sent.
    """
    from urllib.parse import urlsplit

    from mainsequence.client.exceptions import ApiError

    seen: set[int] = set()
    cause: BaseException | None = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ApiError):
            body = cause.payload if isinstance(cause.payload, dict) else {}
            detail = body.get("detail")
            code = _identifier(body.get("code")) or _identifier(
                detail.get("code") if isinstance(detail, dict) else detail
            )
            return {
                "upstream_status": cause.status_code,
                "upstream_method": cause.method,
                "upstream_path": urlsplit(str(cause.url)).path if cause.url else None,
                "upstream_code": code,
                "upstream_error": type(cause).__name__,
            }
        cause = cause.__cause__ or cause.__context__
    return {"upstream_error": type(exc).__name__}


def api_http_error(exc: Exception) -> HTTPException:
    if is_dependency_starting(exc):
        return dependency_starting()
    if isinstance(exc, PlatformSecretAccessError):
        return api_error(
            status_code=502,
            code="secret_resolution_failed",
            message=str(exc),
            retryable=True,
        )
    if isinstance(exc, EncryptionError):
        # The message names the key Secret and the failure kind; never a value or key material.
        return api_error(
            status_code=503,
            code="credential_key_unavailable",
            message=str(exc),
            retryable=True,
        )
    if isinstance(exc, (AccountCredentialsMissingError, StoredCredentialError)):
        return api_error(
            status_code=409,
            code="account_credentials_missing",
            message=str(exc),
        )
    if isinstance(exc, AlpacaCredentialsRejectedError):
        return api_error(
            status_code=400,
            code="alpaca_credentials_rejected",
            message=str(exc),
        )
    if isinstance(exc, AccountOwnedByAnotherError):
        # Never names the owner (ADR 0019).
        return api_error(
            status_code=409,
            code="account_owned_by_another",
            message=str(exc),
        )
    if isinstance(exc, AccountInUseError):
        # Never names the configurations (ADR 0022).
        return api_error(
            status_code=409,
            code="account_in_use",
            message=str(exc),
        )
    if isinstance(exc, PreTradeCheckError):
        # Structured 409: the checks that refused the batch, for the caller's own decision.
        return HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": False,
                "checks": [check.as_payload() for check in exc.checks],
            },
        )
    if isinstance(exc, ValidationError):
        # A provider's payload that its model refuses. pydantic's message dumps the payload's
        # fields and values, so only where and why it failed is logged, and nothing is returned.
        logger.warning(
            "A provider payload failed validation as %s: %s",
            exc.title,
            exc.errors(include_url=False, include_input=False),
        )
        return api_error(
            status_code=502,
            code="provider_failure",
            message=PROVIDER_PAYLOAD_UNREADABLE,
        )
    if isinstance(exc, ValueError):
        # A conflict the connector raises itself, in its own words.
        return api_error(status_code=409, code="action_conflict", message=str(exc))
    from alpaca.common.exceptions import APIError as AlpacaAPIError
    from etfhextractor.exceptions import ETFHoldingsError
    from requests import RequestException

    if isinstance(exc, (AlpacaAPIError, ETFHoldingsError, RequestException)):
        return api_error(
            status_code=502,
            code="provider_failure",
            message="Alpaca or the holdings provider could not complete the operation.",
        )
    if not isinstance(exc, (HTTPException, LookupError, TypeError)):
        upstream = upstream_failure(exc)
        logger.exception(
            "Unhandled API dependency failure: %s %s answered %s (%s)",
            upstream.get("upstream_method"),
            upstream.get("upstream_path"),
            upstream.get("upstream_status"),
            upstream.get("upstream_code") or upstream["upstream_error"],
            extra=upstream,
        )
    return markets_api_http_error(exc)


__all__ = [
    "api_http_error",
    "bad_request",
    "dependency_starting",
    "not_found",
    "upstream_failure",
]
