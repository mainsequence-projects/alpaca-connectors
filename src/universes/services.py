"""Source-based universe preview and materialization services."""

from __future__ import annotations

import uuid
from typing import Any

from src.universes.etf_holdings import (
    build_holdings_asset_category_plan,
    sync_holdings_asset_category,
)
from src.universes.materialized import (
    get_materialized_universe,
    materialized_universe_source_uid,
)
from src.universes.sources import UniverseSource, get_universe_source


def _require_enabled_source(source_uid: uuid.UUID | str) -> UniverseSource:
    source = get_universe_source(source_uid)
    if source is None:
        raise LookupError(f"Universe source {source_uid!s} does not exist.")
    if not source.enabled:
        raise ValueError(f"Universe source {source_uid!s} is disabled.")
    return source


def preview_universe_source(
    source_uid: uuid.UUID | str,
    *,
    timeout: float = 30.0,
) -> Any:
    """Extract and validate one durable source without changing category membership."""
    source = _require_enabled_source(source_uid)
    return build_holdings_asset_category_plan(
        etf_ticker=source.symbol,
        fund_url=source.source_url,
        timeout=timeout,
    )


def sync_universe_source(
    source_uid: uuid.UUID | str,
    *,
    timeout: float = 30.0,
) -> Any:
    """Materialize one source into its holdings-backed AssetCategory after strict validation."""
    plan = preview_universe_source(source_uid, timeout=timeout)
    if plan.has_blockers():
        raise ValueError(
            "Refusing to synchronize the universe because extracted holdings are incomplete "
            "or do not resolve uniquely to registered Main Sequence assets."
        )
    return sync_holdings_asset_category(
        etf_ticker=plan.etf_ticker,
        asset_uids=[
            plan.existing_asset_uids_by_symbol[symbol]
            for symbol in plan.component_symbols
            if symbol in plan.existing_asset_uids_by_symbol
        ],
    )


def _materialized_universe_source_uid(category_uid: uuid.UUID | str) -> str:
    universe = get_materialized_universe(str(category_uid))
    if universe is None:
        raise LookupError(f"Universe {category_uid!s} does not exist.")
    source_uid = materialized_universe_source_uid(universe.get("metadata_json"))
    if source_uid is None:
        raise ValueError(f"Universe {category_uid!s} has no configured extraction source.")
    return source_uid


def preview_materialized_universe(
    category_uid: uuid.UUID | str,
    *,
    timeout: float = 30.0,
) -> Any:
    """Preview the explicit source linked to a created universe."""
    return preview_universe_source(
        _materialized_universe_source_uid(category_uid),
        timeout=timeout,
    )


def run_materialized_universe(
    category_uid: uuid.UUID | str,
    *,
    timeout: float = 30.0,
) -> Any:
    """Synchronize memberships for a previously created and configured universe."""
    return sync_universe_source(
        _materialized_universe_source_uid(category_uid),
        timeout=timeout,
    )


__all__ = [
    "preview_materialized_universe",
    "preview_universe_source",
    "run_materialized_universe",
    "sync_universe_source",
]
