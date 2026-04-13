"""Market metadata extractors."""

from .common import ExpandedSymbolUniverse
from .browser import fetch_page_html_with_playwright
from .invesco import (
    InvescoHoldingsExtractor,
    fetch_invesco_component_tickers,
)
from .ishares import (
    IsharesHoldingsExtractor,
    fetch_ishares_component_tickers,
    parse_ishares_holdings_tickers,
)
from .registry import build_component_extractor
from .state_street import (
    StateStreetHoldingsExtractor,
    fetch_state_street_component_tickers,
)
from .vanguard import (
    VanguardHoldingsExtractor,
    fetch_vanguard_component_tickers,
)

__all__ = [
    "ExpandedSymbolUniverse",
    "InvescoHoldingsExtractor",
    "IsharesHoldingsExtractor",
    "StateStreetHoldingsExtractor",
    "VanguardHoldingsExtractor",
    "build_component_extractor",
    "fetch_page_html_with_playwright",
    "fetch_invesco_component_tickers",
    "fetch_ishares_component_tickers",
    "fetch_state_street_component_tickers",
    "fetch_vanguard_component_tickers",
    "parse_ishares_holdings_tickers",
]
