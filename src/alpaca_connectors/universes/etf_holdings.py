"""Provider-derived holdings used to construct registered asset universes."""

from __future__ import annotations

from typing import Any

from etfhextractor import (
    build_holdings_asset_category_plan as _build_holdings_asset_category_plan,
)
from etfhextractor import (
    build_holdings_asset_category_unique_identifier,
)
from etfhextractor import (
    sync_holdings_asset_category as _sync_holdings_asset_category,
)
from etfhextractor.settings import SUPPORTED_PROVIDERS, normalize_provider_name

SUPPORTED_COMPONENT_PROVIDERS = tuple(SUPPORTED_PROVIDERS)


def _normalize_component_provider(provider: str) -> str:
    normalized_provider = normalize_provider_name(provider)
    if normalized_provider not in SUPPORTED_COMPONENT_PROVIDERS:
        raise ValueError(
            f"Unsupported component provider: {provider!r}. "
            f"Supported providers: {', '.join(SUPPORTED_COMPONENT_PROVIDERS)}"
        )
    return normalized_provider


def build_holdings_asset_category_plan(
    *,
    etf_ticker: str,
    fund_url: str | None = None,
    component_provider: str | None = None,
    timeout: float = 30.0,
    **kwargs: Any,
) -> Any:
    provider = (
        _normalize_component_provider(component_provider)
        if component_provider is not None
        else None
    )
    if provider is None and fund_url is None:
        raise ValueError(
            "Holdings extraction requires a UniverseSource URL or an explicit component provider."
        )

    return _build_holdings_asset_category_plan(
        etf_ticker=etf_ticker,
        fund_url=fund_url,
        component_provider=provider,
        timeout=timeout,
        **kwargs,
    )


def sync_holdings_asset_category(*, etf_ticker: str, asset_uids: list[Any]) -> Any:
    return _sync_holdings_asset_category(etf_ticker=etf_ticker, asset_uids=asset_uids)
