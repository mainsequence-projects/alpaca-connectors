from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

import requests

from etf_extraction.extractors import ExpandedSymbolUniverse, build_component_extractor
from etf_extraction.settings import SUPPORTED_COMPONENT_PROVIDERS


@dataclass(frozen=True)
class EtfExpansionRequest:
    seed_tickers: Sequence[str]
    component_provider: str
    timeout: float = 30.0


@dataclass(frozen=True)
class EtfExpansionResult:
    component_provider: str
    universe: ExpandedSymbolUniverse

    @property
    def symbols_for_registration(self) -> list[str]:
        return self.universe.expanded_symbols


def expand_etf_seed_symbols(
    request: EtfExpansionRequest,
    *,
    requests_session: requests.Session | None = None,
) -> EtfExpansionResult:
    provider = request.component_provider.strip().lower()
    if provider not in SUPPORTED_COMPONENT_PROVIDERS:
        raise ValueError(
            f"Unsupported component provider: {request.component_provider!r}. "
            f"Supported providers: {', '.join(SUPPORTED_COMPONENT_PROVIDERS)}"
        )

    extractor = build_component_extractor(
        provider,
        timeout=request.timeout,
        requests_session=requests_session,
    )
    return EtfExpansionResult(
        component_provider=provider,
        universe=extractor.expand_seed_symbols(request.seed_tickers),
    )
