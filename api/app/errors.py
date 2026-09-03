"""Stable, user-safe HTTP error construction."""

from __future__ import annotations

from fastapi import HTTPException


def bad_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": "invalid_request", "message": message, "retryable": False},
    )


def api_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": str(exc), "retryable": False},
        )
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=409,
            detail={"code": "action_conflict", "message": str(exc), "retryable": False},
        )
    from alpaca.common.exceptions import APIError as AlpacaAPIError
    from etfhextractor.exceptions import ETFHoldingsError
    from requests import RequestException

    if isinstance(exc, (AlpacaAPIError, ETFHoldingsError, RequestException)):
        return HTTPException(
            status_code=502,
            detail={
                "code": "provider_failure",
                "message": "Alpaca or the holdings provider could not complete the operation.",
                "retryable": False,
            },
        )
    return HTTPException(
        status_code=503,
        detail={
            "code": "dependency_unavailable",
            "message": "The operation could not be completed by a required service.",
            "retryable": True,
        },
    )


def not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": message, "retryable": False},
    )


__all__ = ["api_http_error", "bad_request", "not_found"]
