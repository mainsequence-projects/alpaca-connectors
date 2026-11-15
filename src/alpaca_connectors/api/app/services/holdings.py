"""API shaping for account holdings services."""

from __future__ import annotations

from dataclasses import asdict

from alpaca_connectors.holdings import (
    capture_alpaca_account_holdings,
    get_account_holdings_snapshot,
    list_account_holdings,
    plan_alpaca_account_holdings,
)

from .common import collection_response


def list_holdings(
    account_uid: str,
    *,
    limit: int,
    offset: int,
    as_of=None,
    latest_only: bool = False,
    asset_identifiers: list[str] | None = None,
    ordering: str = "-time_index",
):
    rows, total = list_account_holdings(
        account_uid,
        limit=limit,
        offset=offset,
        as_of=as_of,
        latest_only=latest_only,
        asset_identifiers=asset_identifiers,
        ordering=ordering,
    )
    return collection_response(items=rows, total=total, limit=limit, offset=offset)


def get_holdings_snapshot(account_uid: str, holdings_set_uid: str):
    return get_account_holdings_snapshot(account_uid, holdings_set_uid)


def capture_snapshot(account_uid: str):
    """The capture with Alpaca's reported equity, for the sync that sizes the target (ADR 0023)."""
    return asdict(capture_alpaca_account_holdings(account_uid))


def capture_holdings(account_uid: str):
    captured = capture_snapshot(account_uid)
    captured.pop("equity", None)
    return captured


def preflight_holdings(account_uid: str):
    return plan_alpaca_account_holdings(account_uid)


__all__ = [
    "capture_holdings",
    "capture_snapshot",
    "get_holdings_snapshot",
    "list_holdings",
    "preflight_holdings",
]
