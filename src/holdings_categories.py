from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.assets.alpaca_us_equities import (
    AlpacaEquityRegistrationPlan,
    AlpacaEquityRegistrationResolution,
    build_alpaca_us_equity_registration_plan,
    resolve_alpaca_us_equity_registration_plan,
)
from etf_extraction.extractors import ExpandedSymbolUniverse, build_component_extractor
from etf_extraction.settings import ETF_PROVIDER_MAP_NORMALIZED

HOLDINGS_ASSET_CATEGORY_PREFIX = "HOLDINGS__"


def _load_mainsequence_client() -> Any:
    import mainsequence.client as msc

    return msc


def _coerce_asset_id(asset_or_id: int | object) -> int:
    if isinstance(asset_or_id, int):
        return asset_or_id

    asset_id = getattr(asset_or_id, "id", None)
    if isinstance(asset_id, int):
        return asset_id

    raise ValueError(f"Could not coerce asset id from {asset_or_id!r}")


@dataclass(frozen=True)
class AssetCategorySyncResult:
    unique_identifier: str
    display_name: str
    asset_ids: list[int]


@dataclass(frozen=True)
class HoldingsAssetCategoryPlan:
    etf_ticker: str
    provider: str
    category_unique_identifier: str
    expansion: ExpandedSymbolUniverse
    registration_plan: AlpacaEquityRegistrationPlan
    registration_resolution: AlpacaEquityRegistrationResolution
    component_symbols: list[str]
    existing_asset_ids_by_symbol: dict[str, int]
    missing_registered_symbols: list[str]

    def has_blockers(self) -> bool:
        return bool(
            self.expansion.unsupported_seed_symbols
            or self.registration_plan.missing_symbols_from_alpaca
            or self.missing_registered_symbols
        )

    def summary(self) -> dict[str, Any]:
        return {
            "etf_ticker": self.etf_ticker,
            "provider": self.provider,
            "category_unique_identifier": self.category_unique_identifier,
            "seed_symbols": self.expansion.seed_symbols,
            "component_symbol_count": len(self.component_symbols),
            "component_symbols": self.component_symbols,
            "unsupported_seed_symbols": self.expansion.unsupported_seed_symbols,
            "existing_asset_ids_by_symbol": self.existing_asset_ids_by_symbol,
            "missing_registered_symbols": self.missing_registered_symbols,
            "missing_symbols_from_alpaca": self.registration_plan.missing_symbols_from_alpaca,
            "requested_symbol_aliases": self.registration_plan.requested_symbol_aliases,
            "unresolved_symbols": self.registration_plan.unresolved_symbols,
            "warnings_by_symbol": self.registration_plan.warnings_by_symbol,
        }


def build_holdings_asset_category_unique_identifier(etf_ticker: str) -> str:
    normalized_ticker = etf_ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("ETF ticker must not be empty.")
    return f"{HOLDINGS_ASSET_CATEGORY_PREFIX}{normalized_ticker}"


def infer_holdings_component_provider(etf_ticker: str) -> str:
    normalized_ticker = etf_ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("ETF ticker must not be empty.")

    provider = ETF_PROVIDER_MAP_NORMALIZED.get(normalized_ticker)
    if provider is None:
        raise ValueError(
            f"Could not infer component provider for {normalized_ticker}. "
            "Pass --component-provider explicitly."
        )
    return provider


def sync_holdings_asset_category(
    *,
    etf_ticker: str,
    asset_ids: list[int],
) -> AssetCategorySyncResult:
    msc = _load_mainsequence_client()

    unique_identifier = build_holdings_asset_category_unique_identifier(etf_ticker)
    ordered_asset_ids = list(dict.fromkeys(_coerce_asset_id(asset_id) for asset_id in asset_ids))
    description = f"Published holdings assets for ETF {etf_ticker.strip().upper()}."

    category = msc.AssetCategory.get_or_create(
        display_name=unique_identifier,
        unique_identifier=unique_identifier,
        description=description,
    )

    current_asset_ids = [_coerce_asset_id(asset_or_id) for asset_or_id in category.assets]
    if current_asset_ids:
        category = category.remove_assets(current_asset_ids)
    if ordered_asset_ids:
        category.append_assets(asset_ids=ordered_asset_ids)

    category = msc.AssetCategory.get(unique_identifier=unique_identifier)
    return AssetCategorySyncResult(
        unique_identifier=category.unique_identifier,
        display_name=category.display_name,
        asset_ids=[_coerce_asset_id(asset_or_id) for asset_or_id in category.assets],
    )


def build_holdings_asset_category_plan(
    *,
    etf_ticker: str,
    component_provider: str | None = None,
    include_non_tradable: bool = False,
    timeout: float = 30.0,
    build_component_extractor_fn=build_component_extractor,
    build_registration_plan_fn=build_alpaca_us_equity_registration_plan,
    resolve_registration_plan_fn=resolve_alpaca_us_equity_registration_plan,
) -> HoldingsAssetCategoryPlan:
    normalized_ticker = etf_ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("ETF ticker must not be empty.")

    provider = (
        component_provider.strip().lower()
        if component_provider is not None
        else infer_holdings_component_provider(normalized_ticker)
    )
    extractor = build_component_extractor_fn(provider, timeout=timeout)
    expansion = extractor.expand_seed_symbols([normalized_ticker])
    if normalized_ticker in expansion.unsupported_seed_symbols:
        raise ValueError(
            f"Provider {provider!r} does not support seed ticker {normalized_ticker!r}."
        )

    extracted_component_symbols = expansion.component_symbols_by_seed.get(normalized_ticker, [])
    if not extracted_component_symbols:
        raise RuntimeError(
            f"No component symbols were extracted for {normalized_ticker} with provider {provider!r}."
        )

    registration_plan = build_registration_plan_fn(
        symbols=extracted_component_symbols,
        include_non_tradable=include_non_tradable,
        timeout=timeout,
    )
    registration_resolution = resolve_registration_plan_fn(
        registration_plan,
        timeout=timeout,
    )
    existing_asset_ids_by_symbol = dict(
        sorted(registration_resolution.existing_assets_by_symbol.items())
    )
    component_symbols = [asset.symbol for asset in registration_plan.alpaca_assets]
    missing_registered_symbols = sorted(
        match.symbol for match in registration_resolution.missing_matches
    )

    return HoldingsAssetCategoryPlan(
        etf_ticker=normalized_ticker,
        provider=provider,
        category_unique_identifier=build_holdings_asset_category_unique_identifier(
            normalized_ticker
        ),
        expansion=expansion,
        registration_plan=registration_plan,
        registration_resolution=registration_resolution,
        component_symbols=component_symbols,
        existing_asset_ids_by_symbol=existing_asset_ids_by_symbol,
        missing_registered_symbols=missing_registered_symbols,
    )


__all__ = [
    "AssetCategorySyncResult",
    "HOLDINGS_ASSET_CATEGORY_PREFIX",
    "HoldingsAssetCategoryPlan",
    "build_holdings_asset_category_plan",
    "build_holdings_asset_category_unique_identifier",
    "infer_holdings_component_provider",
    "sync_holdings_asset_category",
]
