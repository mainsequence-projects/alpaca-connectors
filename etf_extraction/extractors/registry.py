from __future__ import annotations

import requests

from etf_extraction.extractors.common import HoldingsExtractor
from etf_extraction.extractors.invesco import InvescoHoldingsExtractor
from etf_extraction.extractors.ishares import IsharesHoldingsExtractor
from etf_extraction.extractors.state_street import StateStreetHoldingsExtractor
from etf_extraction.extractors.vanguard import VanguardHoldingsExtractor
from etf_extraction.settings import SUPPORTED_COMPONENT_PROVIDERS


def build_component_extractor(
    provider: str,
    *,
    requests_session: requests.Session | None = None,
    timeout: float = 30.0,
) -> HoldingsExtractor:
    normalized_provider = provider.strip().lower()
    if normalized_provider == "ishares":
        return IsharesHoldingsExtractor(
            requests_session=requests_session,
            timeout=timeout,
        )
    if normalized_provider == "invesco":
        return InvescoHoldingsExtractor(
            requests_session=requests_session,
            timeout=timeout,
        )
    if normalized_provider == "vanguard":
        return VanguardHoldingsExtractor(
            requests_session=requests_session,
            timeout=timeout,
        )
    if normalized_provider == "state_street":
        return StateStreetHoldingsExtractor(
            requests_session=requests_session,
            timeout=timeout,
        )
    raise ValueError(
        f"Unsupported component provider: {provider!r}. Supported providers: {', '.join(SUPPORTED_COMPONENT_PROVIDERS)}"
    )
