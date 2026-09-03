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

# Project namespace slug used as the ms-markets physical-table app segment for project-owned
# MetaTables (e.g. ``alpaca_connectors__<table>``). Matches src.market_data's storage app.
PROJECT_NAMESPACE_SLUG = "alpaca_connectors"

# Market venue suffix for Alpaca account/asset identities (``<token>__ALPACA`` convention).
ALPACA_VENUE = "ALPACA"


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
        raise RuntimeError(f"Failed to retrieve MainSequence secret {secret_name!r}.") from exc

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


# OpenFIGI classification literals.
#
# These previously came from `mainsequence.client.MARKETS_CONSTANTS`, which was removed in
# SDK 4.x (markets concepts moved to ms-markets). They are intentionally not treated as legacy
# SDK constants anymore. They are local string literals matching the canonical OpenFIGI
# `/v3/mapping` response values (Bloomberg taxonomy):
#   - marketSector  -> "Equity"
#   - securityType  -> "Common Stock"
#   - securityType2 -> "ETP" / "REIT"
# FIGI remains the asset identity; these values only classify/filter OpenFIGI candidates and help
# derive AssetType from `security_market_sector`.
FIGI_MARKET_SECTOR_EQUITY = "Equity"
FIGI_SECURITY_TYPE_COMMON_STOCK = "Common Stock"
FIGI_SECURITY_TYPE_ETP = "ETP"
FIGI_SECURITY_TYPE_REIT = "REIT"


def get_figi_market_sector_equity() -> str:
    return FIGI_MARKET_SECTOR_EQUITY


def get_figi_security_type_common_stock() -> str:
    return FIGI_SECURITY_TYPE_COMMON_STOCK


def get_figi_security_type_etp() -> str:
    return FIGI_SECURITY_TYPE_ETP


def get_figi_security_type_reit() -> str:
    return FIGI_SECURITY_TYPE_REIT
