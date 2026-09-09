"""Stable, user-safe HTTP error construction."""

from __future__ import annotations

import logging

from fastapi import HTTPException
from msm.api.http import api_error
from msm.api.http import api_http_error as markets_api_http_error
from msm.api.http import not_found as not_found

from src.platform_secrets import PlatformSecretAccessError

logger = logging.getLogger(__name__)


def bad_request(message: str) -> HTTPException:
    return api_error(status_code=400, code="invalid_request", message=message)


def api_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PlatformSecretAccessError):
        return api_error(
            status_code=502,
            code="secret_resolution_failed",
            message=str(exc),
            retryable=True,
        )
    if isinstance(exc, ValueError):
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
        logger.exception("Unhandled API dependency failure")
    return markets_api_http_error(exc)


__all__ = ["api_http_error", "bad_request", "not_found"]
