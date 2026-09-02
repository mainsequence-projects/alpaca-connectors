"""Alpaca account registration + holdings for ms-markets.

Mirrors the binance connector's ``src/account`` three-layer design:

- ``src/account/__init__.py`` (this module): identity constants + the stable
  ``Account.unique_identifier`` builder used as the ``msm.api.accounts.Account`` upsert key.
- ``src/account/alpaca_account_details.py``: project-owned one-to-one ``AccountTable`` detail
  MetaTable (static metadata) + a timestamped ``AlpacaAccountBalancesStorage`` (point-in-time
  financials).
- ``src/account/services.py``: the only authenticated layer + ``register_alpaca_account(...)``.

Identity is the Alpaca ``account_number`` (stable, venue-authoritative — survives API-key rotation)
plus a venue+environment suffix, e.g. ``010203ABCD__ALPACA`` (live) / ``...__ALPACA_PAPER`` (paper).
The non-reversible ``api_key_fingerprint`` (sha256 of the public key) is stored on the detail row for
audit; the raw key/secret is never persisted.
"""

from __future__ import annotations

import hashlib

from src.settings import ALPACA_VENUE

# Account environments.
ALPACA_LIVE_ENVIRONMENT = "live"
ALPACA_PAPER_ENVIRONMENT = "paper"
ALPACA_ENVIRONMENTS = (ALPACA_LIVE_ENVIRONMENT, ALPACA_PAPER_ENVIRONMENT)

# unique_identifier suffixes (mirrors the project's ``<token>__VENUE`` asset convention).
ALPACA_LIVE_UID_SUFFIX = f"__{ALPACA_VENUE}"
ALPACA_PAPER_UID_SUFFIX = f"__{ALPACA_VENUE}_PAPER"

# Length of the API-key fingerprint stored for audit.
_API_KEY_FINGERPRINT_LENGTH = 16


def environment_for_is_paper(is_paper: bool) -> str:
    """Return the environment label for a paper/live flag."""
    return ALPACA_PAPER_ENVIRONMENT if is_paper else ALPACA_LIVE_ENVIRONMENT


def account_uid_suffix(is_paper: bool) -> str:
    """Return the unique_identifier suffix for a paper/live flag."""
    return ALPACA_PAPER_UID_SUFFIX if is_paper else ALPACA_LIVE_UID_SUFFIX


def api_key_fingerprint(api_key: str) -> str:
    """Return a stable, non-reversible fingerprint of an Alpaca API key.

    Hashes the public API **key** (never the secret), so the same key always resolves to the same
    audit fingerprint without storing or leaking the key itself.
    """
    cleaned = (api_key or "").strip()
    if not cleaned:
        raise ValueError("api_key must be a non-empty string.")
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:_API_KEY_FINGERPRINT_LENGTH]


def build_account_unique_identifier(*, account_number: str, is_paper: bool) -> str:
    """Build the stable ``Account.unique_identifier`` for an Alpaca account.

    Identity is ``<account_number><env-suffix>`` — e.g. ``010203ABCD__ALPACA`` for a live account or
    ``...__ALPACA_PAPER`` for paper. It is the upsert key for ``msm.api.accounts.Account``, so it must
    be stable per Alpaca account and environment. Using ``account_number`` (returned by
    ``GET /v2/account``) instead of an API-key hash keeps identity stable across API-key rotations.
    """
    cleaned = (account_number or "").strip()
    if not cleaned:
        raise ValueError("account_number must be a non-empty string.")
    return f"{cleaned}{account_uid_suffix(is_paper)}"


__all__ = [
    "ALPACA_ENVIRONMENTS",
    "ALPACA_LIVE_ENVIRONMENT",
    "ALPACA_PAPER_ENVIRONMENT",
    "account_uid_suffix",
    "api_key_fingerprint",
    "build_account_unique_identifier",
    "environment_for_is_paper",
]
