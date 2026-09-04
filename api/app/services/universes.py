"""API shaping for registered Asset Universes."""

from __future__ import annotations

from src.market_data.configurations import bar_configurations_for_universe
from src.operations.signal_job_configurations import signal_job_configurations_for_universe
from src.universes import (
    create_asset_universe_configuration,
    delete_asset_universe,
    get_asset_universe_view,
    list_asset_universes,
    preview_asset_universe,
    run_asset_universe,
    update_asset_universe,
)

from ..schemas import (
    AssetUniverseCreateRequest,
    AssetUniverseResponse,
    AssetUniverseUpdateRequest,
)
from .common import collection_response, json_safe


def list_universes(*, limit: int, offset: int, search: str | None, ordering: str):
    items, total = list_asset_universes(
        limit=limit,
        offset=offset,
        search=search,
        ordering=ordering,
    )
    return collection_response(items=items, total=total, limit=limit, offset=offset)


def get_universe(universe_uid: str) -> AssetUniverseResponse | None:
    item = get_asset_universe_view(universe_uid)
    return AssetUniverseResponse.model_validate(item) if item else None


def list_universe_assets(
    universe_uid: str,
    *,
    limit: int,
    offset: int,
    search: str | None,
    ordering: str,
):
    from src.universes import get_asset_universe

    from .assets import list_asset_resources

    universe = get_asset_universe(universe_uid)
    if universe is None:
        raise LookupError(f"Asset Universe {universe_uid} does not exist.")
    return list_asset_resources(
        limit=limit,
        offset=offset,
        search=search,
        ordering=ordering,
        category_uid=str(universe.asset_category_uid),
    )


def create_universe(request: AssetUniverseCreateRequest) -> AssetUniverseResponse:
    item = create_asset_universe_configuration(**request.model_dump())
    return AssetUniverseResponse.model_validate(item)


def preview_universe_run(universe_uid: str, *, account_uid: str, timeout: float) -> dict:
    universe = get_universe(universe_uid)
    if universe is None:
        raise LookupError(f"Asset Universe {universe_uid} does not exist.")
    plan = preview_asset_universe(universe_uid, account_uid=account_uid, timeout=timeout)
    blockers: list[str] = []
    warnings: list[str] = []
    unresolved_from_alpaca = plan.registration_resolution.unresolved_symbols_from_alpaca
    if unresolved_from_alpaca:
        symbols = ", ".join(unresolved_from_alpaca)
        blockers.append(
            f"Universe {universe_uid} contains {len(unresolved_from_alpaca)} extracted symbol(s) "
            f"that the configured Alpaca account cannot resolve: {symbols}."
        )
    reused_off_catalog = plan.registration_resolution.existing_off_catalog_assets_by_symbol
    if reused_off_catalog:
        warnings.append(
            f"Component extraction will reuse {len(reused_off_catalog)} previously registered "
            "canonical Alpaca asset(s) that are absent from the account's current catalog: "
            + ", ".join(sorted(reused_off_catalog))
            + "."
        )
    symbols_to_register = plan.registration_resolution.missing_assets
    if symbols_to_register:
        warnings.append(
            f"Component extraction will register {len(symbols_to_register)} missing "
            f"Alpaca-backed constituent asset(s) through selected account {plan.account_uid} "
            "before refreshing the universe membership."
        )
    return {
        "universe": universe.model_dump(mode="json"),
        "plan_summary": json_safe(plan.summary()),
        "has_blockers": plan.has_blockers(),
        "blockers": blockers,
        "warnings": warnings,
    }


def run_universe(universe_uid: str, *, account_uid: str, timeout: float) -> dict:
    universe = get_universe(universe_uid)
    if universe is None:
        raise LookupError(f"Asset Universe {universe_uid} does not exist.")
    result = run_asset_universe(universe_uid, account_uid=account_uid, timeout=timeout)
    return {
        "universe_uid": universe_uid,
        "source_uid": universe.source_uid,
        "asset_category_uid": universe.asset_category_uid,
        "account_uid": result.account_uid,
        "asset_uids": [str(uid) for uid in result.asset_uids],
        "asset_count": len(result.asset_uids),
        "observed_at": result.observed_at,
        "signal_uid": result.signal_uid,
        "signal_weight_row_count": result.signal_weight_row_count,
        "component_weights_by_symbol": result.component_weights_by_symbol,
        "asset_identifiers_by_symbol": result.asset_identifiers_by_symbol,
        "existing_asset_uids_by_symbol": result.existing_asset_uids_by_symbol,
        "created_asset_uids_by_symbol": result.created_asset_uids_by_symbol,
        "openfigi_unmatched_symbols": result.openfigi_unmatched_symbols,
    }


def update_universe(
    universe_uid: str,
    request: AssetUniverseUpdateRequest,
) -> AssetUniverseResponse:
    item = update_asset_universe(
        universe_uid,
        **request.model_dump(exclude_unset=True),
    )
    return AssetUniverseResponse.model_validate(item)


def delete_universe(universe_uid: str) -> dict:
    return delete_asset_universe(universe_uid)


def universe_delete_blockers(universe_uid: str) -> list[str]:
    configurations = bar_configurations_for_universe(universe_uid)
    signal_configurations = signal_job_configurations_for_universe(universe_uid)
    return [
        f"Universe {universe_uid} is referenced by bar configuration "
        f"{configuration.name} ({configuration.uid!s})."
        for configuration in configurations
    ] + [
        f"Universe {universe_uid} is referenced by signal Job configuration "
        f"{configuration.name} ({configuration.uid!s})."
        for configuration in signal_configurations
    ]


__all__ = [
    "create_universe",
    "delete_universe",
    "get_universe",
    "list_universe_assets",
    "list_universes",
    "preview_universe_run",
    "run_universe",
    "universe_delete_blockers",
    "update_universe",
]
