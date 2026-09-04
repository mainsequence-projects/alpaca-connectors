"""Source preview and registered AssetUniverse Run services."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, replace
from typing import Any

from src.assets import (
    AlpacaEquityRegistrationPlan,
    AlpacaEquityRegistrationResolution,
    build_alpaca_us_equity_registration_plan,
    register_alpaca_us_equity_assets,
    resolve_alpaca_us_equity_registration_plan,
)
from src.universes.etf_holdings import build_holdings_asset_category_plan
from src.universes.materialized import require_asset_universe_links
from src.universes.sources import UniverseSource, get_universe_source


@dataclass(frozen=True, slots=True)
class AssetUniverseRunPlan:
    """Complete read-only plan for a Universe plus its selected execution account."""

    universe_uid: str
    source_uid: str
    asset_category_uid: str
    account_uid: str
    timeout: float
    observed_at: dt.datetime
    holdings_plan: Any
    component_weights_by_symbol: dict[str, float]
    registration_plan: AlpacaEquityRegistrationPlan
    registration_resolution: AlpacaEquityRegistrationResolution

    def has_blockers(self) -> bool:
        return bool(self.registration_resolution.unresolved_symbols_from_alpaca)

    def summary(self) -> dict[str, Any]:
        return {
            "universe_uid": self.universe_uid,
            "source_uid": self.source_uid,
            "asset_category_uid": self.asset_category_uid,
            "account_uid": self.account_uid,
            "observed_at": self.observed_at,
            "provider": self.holdings_plan.provider,
            "fund_name": self.holdings_plan.fund_holdings.fund_name,
            "as_of_date": self.holdings_plan.fund_holdings.as_of_date,
            "source_url": self.holdings_plan.fund_holdings.url,
            "component_symbol_count": len(self.holdings_plan.component_symbols),
            "component_symbols": list(self.holdings_plan.component_symbols),
            "component_weights_by_symbol": dict(self.component_weights_by_symbol),
            "existing_asset_uids_by_symbol": dict(
                self.registration_resolution.existing_assets_by_symbol
            ),
            "symbols_to_register": sorted(
                asset.symbol for asset in self.registration_resolution.missing_assets
            ),
            "missing_symbols_from_alpaca": list(self.registration_plan.missing_symbols_from_alpaca),
            "reused_off_catalog_assets_by_symbol": {
                symbol: reference.model_dump(mode="json")
                for symbol, reference in (
                    self.registration_resolution.existing_off_catalog_assets_by_symbol.items()
                )
            },
            "unresolved_symbols_from_alpaca": list(
                self.registration_resolution.unresolved_symbols_from_alpaca
            ),
            "openfigi_unmatched_symbols": list(self.registration_plan.openfigi_unmatched_symbols),
            "warnings_by_symbol": dict(self.registration_plan.warnings_by_symbol),
        }


@dataclass(frozen=True, slots=True)
class AssetUniverseRunResult:
    """One extracted, registered, and materialized Universe observation."""

    unique_identifier: str
    display_name: str
    asset_uids: list[uuid.UUID]
    account_uid: str
    observed_at: dt.datetime
    component_weights_by_symbol: dict[str, float]
    asset_identifiers_by_symbol: dict[str, str]
    existing_asset_uids_by_symbol: dict[str, str]
    created_asset_uids_by_symbol: dict[str, str]
    openfigi_unmatched_symbols: list[str]
    signal_uid: str | None = None
    signal_weight_row_count: int = 0


def _defer_constituent_resolution_to_alpaca_registration(
    *,
    component_symbols: list[str],
) -> tuple[dict[str, uuid.UUID], list[str], list[str]]:
    """Keep extraction read-only; provider-native registration resolves canonical assets."""
    return {}, list(component_symbols), []


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
    """Extract and validate one durable source without materializing a category."""
    source = _require_enabled_source(source_uid)
    return build_holdings_asset_category_plan(
        etf_ticker=source.symbol,
        fund_url=source.source_url,
        timeout=timeout,
    )


def preview_asset_universe(
    universe_uid: uuid.UUID | str,
    *,
    account_uid: uuid.UUID | str,
    timeout: float = 30.0,
) -> AssetUniverseRunPlan:
    """Build the complete extraction and automatic asset-registration Run plan."""
    universe, source, _category = require_asset_universe_links(universe_uid)
    if not universe.is_active:
        raise ValueError(f"Asset Universe {universe_uid!s} is inactive.")
    if not source.enabled:
        raise ValueError(f"Universe source {source.uid!s} is disabled.")
    holdings_plan = build_holdings_asset_category_plan(
        etf_ticker=source.symbol,
        fund_url=source.source_url,
        timeout=timeout,
        resolve_existing_assets_by_ticker_fn=(_defer_constituent_resolution_to_alpaca_registration),
    )
    observed_at = dt.datetime.now(dt.UTC)
    from etfhextractor import derive_component_weights_from_holdings

    component_weights_by_symbol = derive_component_weights_from_holdings(
        holdings_plan.fund_holdings,
        allowed_asset_classes=("Equity",),
    )
    if not component_weights_by_symbol:
        raise RuntimeError(
            f"Asset Universe {universe_uid!s} extracted no equity component weights."
        )
    registration_plan = build_alpaca_us_equity_registration_plan(
        account_uid=str(account_uid),
        symbols=holdings_plan.component_symbols,
        timeout=timeout,
        enrich_openfigi=False,
    )
    registration_resolution = resolve_alpaca_us_equity_registration_plan(
        registration_plan,
        timeout=timeout,
    )
    return AssetUniverseRunPlan(
        universe_uid=str(universe.uid),
        source_uid=str(universe.source_uid),
        asset_category_uid=str(universe.asset_category_uid),
        account_uid=str(account_uid),
        timeout=timeout,
        observed_at=observed_at,
        holdings_plan=holdings_plan,
        component_weights_by_symbol=dict(sorted(component_weights_by_symbol.items())),
        registration_plan=registration_plan,
        registration_resolution=registration_resolution,
    )


def asset_identifiers_by_symbol_for_universe_plan(
    plan: AssetUniverseRunPlan,
) -> dict[str, str]:
    """Resolve planned symbols to canonical identifiers from Alpaca's immutable IDs."""
    from src.assets.alpaca_asset_details import build_alpaca_unique_identifier

    alpaca_assets_by_symbol = {
        asset.symbol: asset for asset in plan.registration_plan.alpaca_assets
    }
    identifiers_by_symbol: dict[str, str] = {}
    for requested_symbol in plan.holdings_plan.component_symbols:
        alpaca_symbol = plan.registration_plan.requested_symbol_aliases.get(
            requested_symbol,
            requested_symbol,
        )
        alpaca_asset = alpaca_assets_by_symbol.get(alpaca_symbol)
        if alpaca_asset is None:
            registered_reference = (
                plan.registration_resolution.existing_off_catalog_assets_by_symbol.get(
                    requested_symbol
                )
            )
            if registered_reference is not None:
                identifiers_by_symbol[requested_symbol] = registered_reference.unique_identifier
            continue
        identifiers_by_symbol[requested_symbol] = build_alpaca_unique_identifier(
            alpaca_asset.alpaca_asset_id
        )
    return dict(sorted(identifiers_by_symbol.items()))


def asset_identifiers_for_universe_plan(plan: AssetUniverseRunPlan) -> list[str]:
    """Return the deduplicated canonical signal scope for a prepared Universe plan."""
    return sorted(set(asset_identifiers_by_symbol_for_universe_plan(plan).values()))


def materialize_asset_universe(
    universe_uid: uuid.UUID | str,
    *,
    account_uid: uuid.UUID | str,
    timeout: float = 30.0,
    plan: AssetUniverseRunPlan | None = None,
) -> AssetUniverseRunResult:
    """Extract once, bulk-register assets, and bulk-replace linked memberships."""
    from msm.api.assets import AssetCategory

    universe, _source, category = require_asset_universe_links(universe_uid)
    if not universe.is_active:
        raise ValueError(f"Asset Universe {universe_uid!s} is inactive.")
    if plan is None:
        plan = preview_asset_universe(universe_uid, account_uid=account_uid, timeout=timeout)
    elif str(plan.universe_uid) != str(universe_uid) or str(plan.account_uid) != str(account_uid):
        raise ValueError("Prepared Universe plan does not match the requested execution.")
    execution_timeout = plan.timeout
    if plan.has_blockers():
        missing = ", ".join(plan.registration_resolution.unresolved_symbols_from_alpaca)
        raise ValueError(
            "Asset Universe component extraction cannot continue because Alpaca did not resolve "
            f"these extracted symbols: {missing}. No category memberships were changed."
        )
    registration = register_alpaca_us_equity_assets(
        registration_resolution=plan.registration_resolution,
        timeout=execution_timeout,
    )
    assets_by_symbol = registration["assets"]
    from src.assets.alpaca_asset_details import build_alpaca_unique_identifier

    alpaca_assets_by_symbol = {
        asset.symbol: asset for asset in plan.registration_plan.alpaca_assets
    }
    ordered_asset_uids: list[uuid.UUID] = []
    seen_asset_uids: set[uuid.UUID] = set()
    asset_identifiers_by_symbol: dict[str, str] = {}
    for requested_symbol in plan.holdings_plan.component_symbols:
        alpaca_symbol = plan.registration_plan.requested_symbol_aliases.get(
            requested_symbol,
            requested_symbol,
        )
        asset_uid = assets_by_symbol.get(alpaca_symbol)
        if asset_uid is None:
            raise RuntimeError(
                "Asset Universe component extraction produced an incomplete constituent set; "
                f"missing result for {requested_symbol}. No category memberships were changed."
            )
        normalized_asset_uid = uuid.UUID(str(asset_uid))
        if normalized_asset_uid not in seen_asset_uids:
            ordered_asset_uids.append(normalized_asset_uid)
            seen_asset_uids.add(normalized_asset_uid)
        provider_asset = alpaca_assets_by_symbol.get(alpaca_symbol)
        if provider_asset is None:
            registered_reference = (
                plan.registration_resolution.existing_off_catalog_assets_by_symbol.get(
                    requested_symbol
                )
            )
            if registered_reference is None:
                raise RuntimeError(
                    "Asset Universe registration plan lost the provider identity for "
                    f"{requested_symbol}. No category memberships were changed."
                )
            asset_identifiers_by_symbol[requested_symbol] = (
                registered_reference.unique_identifier
            )
            continue
        asset_identifiers_by_symbol[requested_symbol] = build_alpaca_unique_identifier(
            provider_asset.alpaca_asset_id
        )
    AssetCategory.replace_memberships(
        category_uid=category.uid,
        asset_uids=ordered_asset_uids,
    )
    return AssetUniverseRunResult(
        unique_identifier=category.unique_identifier,
        display_name=category.display_name,
        asset_uids=ordered_asset_uids,
        account_uid=plan.account_uid,
        observed_at=plan.observed_at,
        component_weights_by_symbol=dict(plan.component_weights_by_symbol),
        asset_identifiers_by_symbol=asset_identifiers_by_symbol,
        existing_asset_uids_by_symbol=dict(registration["existing_assets"]),
        created_asset_uids_by_symbol=dict(registration["created_assets"]),
        openfigi_unmatched_symbols=list(registration["openfigi_unmatched_symbols"]),
    )


def run_asset_universe(
    universe_uid: uuid.UUID | str,
    *,
    account_uid: uuid.UUID | str,
    timeout: float = 30.0,
) -> AssetUniverseRunResult:
    """Publish a Universe extraction through the canonical Alpaca ETF signal."""
    from src.portfolios.alpaca_etf_signal import build_alpaca_etf_holdings_signal

    plan = preview_asset_universe(universe_uid, account_uid=account_uid, timeout=timeout)
    signal = build_alpaca_etf_holdings_signal(
        universe_uid=str(universe_uid),
        account_uid=str(account_uid),
        prepared_plan=plan,
    )
    error_on_last_update, frame = signal.run()
    if error_on_last_update:
        raise RuntimeError(
            f"Asset Universe {universe_uid!s} signal update failed; see DataNode logs."
        )
    result = signal.last_universe_materialization
    if result is None:
        raise RuntimeError(
            f"Asset Universe {universe_uid!s} signal update returned no materialization result."
        )
    return replace(
        result,
        signal_uid=signal.signal_uid,
        signal_weight_row_count=0 if frame is None else int(frame.shape[0]),
    )


__all__ = [
    "AssetUniverseRunPlan",
    "AssetUniverseRunResult",
    "asset_identifiers_by_symbol_for_universe_plan",
    "asset_identifiers_for_universe_plan",
    "materialize_asset_universe",
    "preview_asset_universe",
    "preview_universe_source",
    "run_asset_universe",
]
