from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import yaml

OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_MAX_JOBS_WITHOUT_API_KEY = 10
OPENFIGI_MAX_JOBS_WITH_API_KEY = 100
OPENFIGI_DEFAULT_TIMEOUT = 30.0
OPENFIGI_DEFAULT_EXCHANGE_CODE = "US"
ALPACA_API_KEY_SECRET_NAME = "ALPACA_API_KEY"
ALPACA_SECRET_KEY_SECRET_NAME = "ALPACA_SECRET_KEY"


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

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SEED_UNIVERSES_PATH = DATA_DIR / "seed_universes.yaml"


@lru_cache(maxsize=8)
def get_platform_secret_value(secret_name: str) -> str | None:
    try:
        import mainsequence.client as msc
    except Exception as exc:  # pragma: no cover - import failure depends on runtime env
        raise RuntimeError(
            f"Failed to import MainSequence client while resolving secret {secret_name!r}."
        ) from exc

    try:
        secret = msc.Secret.get_or_none(name=secret_name)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to retrieve MainSequence secret {secret_name!r}."
        ) from exc

    if secret is None or secret.value is None:
        return None

    if hasattr(secret.value, "get_secret_value"):
        return secret.value.get_secret_value()
    return str(secret.value)


def get_alpaca_api_key() -> str:
    env_value = os.getenv(ALPACA_API_KEY_SECRET_NAME)
    if env_value:
        return env_value
    secret_value = get_platform_secret_value(ALPACA_API_KEY_SECRET_NAME)
    if secret_value:
        return secret_value
    raise RuntimeError(
        "Missing Alpaca API key. Set ALPACA_API_KEY in the environment or create the "
        f"MainSequence secret {ALPACA_API_KEY_SECRET_NAME!r}."
    )


def get_alpaca_secret_key() -> str:
    env_value = os.getenv(ALPACA_SECRET_KEY_SECRET_NAME)
    if env_value:
        return env_value
    secret_value = get_platform_secret_value(ALPACA_SECRET_KEY_SECRET_NAME)
    if secret_value:
        return secret_value
    raise RuntimeError(
        "Missing Alpaca secret key. Set ALPACA_SECRET_KEY in the environment or create the "
        f"MainSequence secret {ALPACA_SECRET_KEY_SECRET_NAME!r}."
    )


def get_openfigi_api_key() -> str | None:
    return os.getenv("OPENFIGI_API_KEY") or os.getenv("FIGI_API_KEY")


@lru_cache(maxsize=1)
def get_seed_universes() -> dict[str, list[str]]:
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


MAG_7_CATEGORY_SYMBOLS = get_seed_universes()["mag_7_category_symbols"]
ETFS_MAIN_TICKERS = get_seed_universes()["etfs_main_tickers"]
ETF_PROVIDER_MAP_NORMALIZED = get_seed_universes()["etf_provider_map_normalized"]
SUPPORTED_COMPONENT_PROVIDERS = ("ishares", "invesco", "vanguard", "state_street")


@lru_cache(maxsize=1)
def get_markets_constants():
    import mainsequence.client as msc

    return msc.MARKETS_CONSTANTS


def get_figi_market_sector_equity() -> str:
    return get_markets_constants().FIGI_MARKET_SECTOR_EQUITY


def get_figi_security_type_common_stock() -> str:
    return get_markets_constants().FIGI_SECURITY_TYPE_COMMON_STOCK


def get_figi_security_type_etp() -> str:
    return get_markets_constants().FIGI_SECURITY_TYPE_ETP


def get_figi_security_type_reit() -> str:
    return get_markets_constants().FIGI_SECURITY_TYPE_REIT


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
