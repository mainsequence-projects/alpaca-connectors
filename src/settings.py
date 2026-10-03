from __future__ import annotations

import os

OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_MAX_JOBS_WITHOUT_API_KEY = 10
OPENFIGI_MAX_JOBS_WITH_API_KEY = 100
OPENFIGI_DEFAULT_TIMEOUT = 30.0
OPENFIGI_DEFAULT_EXCHANGE_CODE = "US"

# Project namespace slug used as the ms-markets physical-table app segment for project-owned
# MetaTables (e.g. ``alpaca_connectors__<table>``). Matches src.market_data's storage app.
PROJECT_NAMESPACE_SLUG = "alpaca_connectors"

# Market venue suffix for Alpaca account/asset identities (``<token>__ALPACA`` convention).
ALPACA_VENUE = "ALPACA"


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
# These values only classify/filter optional OpenFIGI candidates. Connector-owned asset identity
# and AssetType come from Alpaca's immutable asset UUID and explicit asset-class mapping.
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
