from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from etf_extraction.extractors import ExpandedSymbolUniverse, build_component_extractor
from etf_extraction.settings import ETF_PROVIDER_MAP_NORMALIZED

HOLDINGS_ASSET_CATEGORY_PREFIX = "HOLDINGS__"


@dataclass(frozen=True)
class AssetCategorySyncResult:
    unique_identifier: str
    display_name: str
    # ms-markets asset identity is a UUID (Asset.uid) serialized as a string. The field name is
    # kept for compatibility with existing CLI/consumers; values are now uid strings (D5).
    asset_ids: list[str]


@dataclass(frozen=True)
class HoldingsAssetCategoryPlan:
    etf_ticker: str
    provider: str
    category_unique_identifier: str
    expansion: ExpandedSymbolUniverse
    component_symbols: list[str]
    # Maps component ticker -> asset uid string (was integer Asset.id under the old SDK).
    existing_asset_ids_by_symbol: dict[str, str]
    missing_registered_symbols: list[str]
    ambiguous_registered_symbols: list[str]

    def has_blockers(self) -> bool:
        return bool(
            self.expansion.unsupported_seed_symbols
            or self.missing_registered_symbols
            or self.ambiguous_registered_symbols
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
            "ambiguous_registered_symbols": self.ambiguous_registered_symbols,
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
    asset_ids: list[str],
) -> AssetCategorySyncResult:
    """Create/refresh an ``HOLDINGS__<ETF>`` category and atomically replace its members.

    ``asset_ids`` are ms-markets asset uid strings. ``AssetCategory.upsert`` is the get-or-create,
    and ``replace_memberships`` is the atomic delete-all-then-insert that the old
    ``remove_assets`` + ``append_assets`` pair performed.
    """
    from msm.api.assets import AssetCategory

    unique_identifier = build_holdings_asset_category_unique_identifier(etf_ticker)
    ordered_asset_uids = list(dict.fromkeys(asset_ids))
    description = f"Published holdings assets for ETF {etf_ticker.strip().upper()}."

    category = AssetCategory.upsert(
        unique_identifier=unique_identifier,
        display_name=unique_identifier,
        description=description,
    )

    memberships = AssetCategory.replace_memberships(
        category_uid=category.uid,
        asset_uids=ordered_asset_uids,
    )

    return AssetCategorySyncResult(
        unique_identifier=category.unique_identifier,
        display_name=category.display_name,
        asset_ids=[str(membership.asset_uid) for membership in memberships],
    )


def resolve_existing_assets_by_ticker(
    *,
    component_symbols: list[str],
) -> tuple[dict[str, str], list[str], list[str]]:
    if not component_symbols:
        return {}, [], []

    from src.assets.resolution import assets_for_ticker

    existing_asset_uids_by_symbol: dict[str, str] = {}
    ambiguous_registered_symbols: list[str] = []
    missing_registered_symbols: list[str] = []

    for symbol in component_symbols:
        assets = assets_for_ticker(symbol)
        if not assets:
            missing_registered_symbols.append(symbol)
            continue
        if len(assets) != 1:
            ambiguous_registered_symbols.append(symbol)
            continue
        existing_asset_uids_by_symbol[symbol] = str(assets[0].uid)

    return (
        dict(sorted(existing_asset_uids_by_symbol.items())),
        sorted(missing_registered_symbols),
        sorted(ambiguous_registered_symbols),
    )


def build_holdings_asset_category_plan(
    *,
    etf_ticker: str,
    component_provider: str | None = None,
    include_non_tradable: bool = False,
    timeout: float = 30.0,
    build_component_extractor_fn=build_component_extractor,
    resolve_existing_assets_by_ticker_fn=resolve_existing_assets_by_ticker,
) -> HoldingsAssetCategoryPlan:
    del include_non_tradable

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

    existing_asset_ids_by_symbol, missing_registered_symbols, ambiguous_registered_symbols = (
        resolve_existing_assets_by_ticker_fn(component_symbols=extracted_component_symbols)
    )

    return HoldingsAssetCategoryPlan(
        etf_ticker=normalized_ticker,
        provider=provider,
        category_unique_identifier=build_holdings_asset_category_unique_identifier(
            normalized_ticker
        ),
        expansion=expansion,
        component_symbols=extracted_component_symbols,
        existing_asset_ids_by_symbol=existing_asset_ids_by_symbol,
        missing_registered_symbols=missing_registered_symbols,
        ambiguous_registered_symbols=ambiguous_registered_symbols,
    )


__all__ = [
    "AssetCategorySyncResult",
    "HOLDINGS_ASSET_CATEGORY_PREFIX",
    "HoldingsAssetCategoryPlan",
    "build_holdings_asset_category_plan",
    "build_holdings_asset_category_unique_identifier",
    "infer_holdings_component_provider",
    "resolve_existing_assets_by_ticker",
    "sync_holdings_asset_category",
]
