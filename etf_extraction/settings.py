from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class IsharesHoldingsSource:
    requested_ticker: str
    holdings_ticker: str
    product_listing_url: str


@dataclass(frozen=True)
class InvescoHoldingsSource:
    requested_ticker: str
    holdings_ticker: str
    landing_url: str


@dataclass(frozen=True)
class VanguardHoldingsSource:
    requested_ticker: str
    profile_url: str


@dataclass(frozen=True)
class StateStreetHoldingsSource:
    requested_ticker: str
    holdings_ticker: str
    quick_info_url: str


ISHARES_PRODUCT_LISTING_URL = "https://www.ishares.com/us/products/etf-investments"
INVESCO_HOLDINGS_LANDING_URL_TEMPLATE = (
    "https://www.invesco.com/us/financial-products/etfs/holdings"
    "?audienceType=Investor&ticker={ticker}"
)
VANGUARD_PROFILE_URL_TEMPLATE = (
    "https://investor.vanguard.com/investment-products/etfs/profile/{ticker}#portfolio-composition"
)
STATE_STREET_QUICK_INFO_URL_TEMPLATE = (
    "https://www.ssga.com/bin/v1/ssmp/fund/productquickinfo"
    "?country=us&language=en&role=intermediary&ticker%5B%5D={ticker}"
)

ETF_EXTRACTION_DIR = Path(__file__).resolve().parent
ETF_DATA_DIR = ETF_EXTRACTION_DIR / "data"
SEED_UNIVERSES_PATH = ETF_DATA_DIR / "seed_universes.yaml"
SUPPORTED_COMPONENT_PROVIDERS = ("ishares", "invesco", "vanguard", "state_street")


@lru_cache(maxsize=1)
def get_seed_universes() -> dict[str, list[str] | dict[str, str]]:
    with SEED_UNIVERSES_PATH.open() as fh:
        data = yaml.safe_load(fh)
    return {
        key: (
            [str(value).upper() for value in values]
            if isinstance(values, list)
            else {str(subkey).upper(): str(subvalue) for subkey, subvalue in values.items()}
        )
        for key, values in data.items()
    }


def get_etf_provider_map() -> dict[str, str]:
    return dict(get_seed_universes()["etf_provider_map_normalized"])


ETF_PROVIDER_MAP_NORMALIZED = get_etf_provider_map()
MAG_7_CATEGORY_SYMBOLS = list(get_seed_universes()["mag_7_category_symbols"])
ETFS_MAIN_TICKERS = list(get_seed_universes()["etfs_main_tickers"])


def get_ishares_holdings_source(ticker: str) -> IsharesHoldingsSource | None:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        return None
    return IsharesHoldingsSource(
        requested_ticker=normalized_ticker,
        holdings_ticker=normalized_ticker,
        product_listing_url=ISHARES_PRODUCT_LISTING_URL,
    )


def get_invesco_holdings_source(ticker: str) -> InvescoHoldingsSource | None:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        return None
    return InvescoHoldingsSource(
        requested_ticker=normalized_ticker,
        holdings_ticker=normalized_ticker,
        landing_url=INVESCO_HOLDINGS_LANDING_URL_TEMPLATE.format(ticker=normalized_ticker),
    )


def get_vanguard_holdings_source(ticker: str) -> VanguardHoldingsSource | None:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        return None
    return VanguardHoldingsSource(
        requested_ticker=normalized_ticker,
        profile_url=VANGUARD_PROFILE_URL_TEMPLATE.format(ticker=normalized_ticker.lower()),
    )


def get_state_street_holdings_source(ticker: str) -> StateStreetHoldingsSource | None:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        return None
    return StateStreetHoldingsSource(
        requested_ticker=normalized_ticker,
        holdings_ticker=normalized_ticker,
        quick_info_url=STATE_STREET_QUICK_INFO_URL_TEMPLATE.format(
            ticker=normalized_ticker.lower()
        ),
    )
