from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


def _coerce_asset_ticker(asset: Any) -> str | None:
    ticker = getattr(asset, "ticker", None)
    if ticker is None and getattr(asset, "current_snapshot", None) is not None:
        ticker = asset.current_snapshot.ticker
    if not ticker:
        return None
    return str(ticker).strip().upper()


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
    component_symbols: list[str]
    existing_asset_ids_by_symbol: dict[str, int]
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


def resolve_existing_assets_by_ticker(
    *,
    component_symbols: list[str],
) -> tuple[dict[str, int], list[str], list[str]]:
    if not component_symbols:
        return {}, [], []

    msc = _load_mainsequence_client()
    matching_assets = msc.Asset.filter(current_snapshot__ticker__in=component_symbols)
    assets_by_ticker: dict[str, list[Any]] = {}
    for asset in matching_assets:
        ticker = _coerce_asset_ticker(asset)
        if ticker is None:
            continue
        assets_by_ticker.setdefault(ticker, []).append(asset)

    existing_asset_ids_by_symbol: dict[str, int] = {}
    ambiguous_registered_symbols: list[str] = []
    missing_registered_symbols: list[str] = []

    for symbol in component_symbols:
        assets = assets_by_ticker.get(symbol, [])
        if not assets:
            missing_registered_symbols.append(symbol)
            continue
        if len(assets) != 1:
            ambiguous_registered_symbols.append(symbol)
            continue
        existing_asset_ids_by_symbol[symbol] = assets[0].id

    return (
        dict(sorted(existing_asset_ids_by_symbol.items())),
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
