from __future__ import annotations

import requests

from src.extractors.common import HoldingsExtractor
from src.extractors.invesco import InvescoHoldingsExtractor
from src.extractors.ishares import IsharesHoldingsExtractor
from src.extractors.state_street import StateStreetHoldingsExtractor
from src.extractors.vanguard import VanguardHoldingsExtractor
from src.settings import SUPPORTED_COMPONENT_PROVIDERS


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
