"""Provider-derived holdings used to construct registered asset universes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from etfhextractor import (
    ETFHoldingsReader,
    build_holdings_asset_category_unique_identifier,
    derive_component_symbols_from_holdings,
)
from etfhextractor import (
    build_holdings_asset_category_plan as _build_holdings_asset_category_plan,
)
from etfhextractor import (
    sync_holdings_asset_category as _sync_holdings_asset_category,
)
from etfhextractor.settings import SUPPORTED_PROVIDERS, normalize_provider_name

SUPPORTED_COMPONENT_PROVIDERS = tuple(SUPPORTED_PROVIDERS)


@dataclass(frozen=True, slots=True)
class ExpandedSymbolUniverse:
    seed_symbols: list[str]
    expanded_symbols: list[str]
    component_symbols_by_seed: dict[str, list[str]]
    unsupported_seed_symbols: list[str]


@dataclass(frozen=True, slots=True)
class EtfExpansionRequest:
    seed_tickers: Sequence[str]
    component_provider: str
    timeout: float = 30.0


@dataclass(frozen=True, slots=True)
class EtfExpansionResult:
    component_provider: str
    universe: ExpandedSymbolUniverse

    @property
    def symbols_for_registration(self) -> list[str]:
        return self.universe.expanded_symbols


def _normalize_ticker(value: str) -> str:
    return value.strip().upper()


def _normalize_seed_symbols(seed_tickers: Sequence[str]) -> list[str]:
    return sorted(
        {normalized for value in seed_tickers if (normalized := _normalize_ticker(str(value)))}
    )


def _normalize_component_provider(provider: str) -> str:
    normalized_provider = normalize_provider_name(provider)
    if normalized_provider not in SUPPORTED_COMPONENT_PROVIDERS:
        raise ValueError(
            f"Unsupported component provider: {provider!r}. "
            f"Supported providers: {', '.join(SUPPORTED_COMPONENT_PROVIDERS)}"
        )
    return normalized_provider


def expand_etf_seed_symbols(
    request: EtfExpansionRequest,
    *,
    requests_session: Any | None = None,
    reader: ETFHoldingsReader | None = None,
) -> EtfExpansionResult:
    del requests_session

    provider = _normalize_component_provider(request.component_provider)
    seed_symbols = _normalize_seed_symbols(request.seed_tickers)
    holdings_reader = reader or ETFHoldingsReader(timeout=request.timeout)
    component_symbols_by_seed: dict[str, list[str]] = {}
    expanded_symbols = set(seed_symbols)

    for seed_symbol in seed_symbols:
        fund_holdings = holdings_reader.read_ticker(seed_symbol, provider=provider)
        component_symbols = derive_component_symbols_from_holdings(fund_holdings)
        component_symbols_by_seed[seed_symbol] = component_symbols
        expanded_symbols.update(component_symbols)

    return EtfExpansionResult(
        component_provider=provider,
        universe=ExpandedSymbolUniverse(
            seed_symbols=seed_symbols,
            expanded_symbols=sorted(expanded_symbols),
            component_symbols_by_seed=component_symbols_by_seed,
            unsupported_seed_symbols=[],
        ),
    )


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
