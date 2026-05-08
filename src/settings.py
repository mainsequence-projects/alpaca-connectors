from __future__ import annotations

import os
from functools import lru_cache

OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_MAX_JOBS_WITHOUT_API_KEY = 10
OPENFIGI_MAX_JOBS_WITH_API_KEY = 100
OPENFIGI_DEFAULT_TIMEOUT = 30.0
OPENFIGI_DEFAULT_EXCHANGE_CODE = "US"
ALPACA_API_KEY_SECRET_NAME = "ALPACA_API_KEY"
ALPACA_SECRET_KEY_SECRET_NAME = "ALPACA_SECRET_KEY"


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
