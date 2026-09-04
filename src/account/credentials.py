"""Secret-name-only Alpaca credential resolution.

Public application surfaces pass Main Sequence Secret *names*.  Values are resolved only at the
provider boundary, kept in memory for the client constructor, and never returned or persisted.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.platform_secrets import read_platform_secret_value


def normalize_secret_name(value: str, *, field_name: str) -> str:
    """Validate a user-supplied Main Sequence Secret name without resolving its value."""
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty Main Sequence Secret name.")
    if len(normalized) > 255:
        raise ValueError(f"{field_name} must be at most 255 characters.")
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{field_name} must not contain whitespace.")
    return normalized


@dataclass(frozen=True, slots=True)
class AlpacaSecretNames:
    """Durable names of the two Main Sequence Secrets used by an Alpaca account."""

    api_key_secret_name: str
    secret_key_secret_name: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "api_key_secret_name",
            normalize_secret_name(
                self.api_key_secret_name,
                field_name="api_key_secret_name",
            ),
        )
        object.__setattr__(
            self,
            "secret_key_secret_name",
            normalize_secret_name(
                self.secret_key_secret_name,
                field_name="secret_key_secret_name",
            ),
        )


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedAlpacaCredentials:
    """Ephemeral credential values.  Repr is disabled to prevent accidental log disclosure."""

    api_key: str
    secret_key: str


def _secret_value(secret_name: str) -> str:
    return read_platform_secret_value(secret_name)


def resolve_alpaca_credentials(secret_names: AlpacaSecretNames) -> ResolvedAlpacaCredentials:
    """Resolve both configured secrets immediately before constructing an Alpaca client."""
    return ResolvedAlpacaCredentials(
        api_key=_secret_value(secret_names.api_key_secret_name),
        secret_key=_secret_value(secret_names.secret_key_secret_name),
    )


def build_alpaca_trading_client(
    *,
    credentials: ResolvedAlpacaCredentials,
    paper: bool,
):
    """Build a trading client from ephemeral credentials."""
    from alpaca.trading.client import TradingClient

    return TradingClient(
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        paper=paper,
    )


def build_alpaca_historical_data_client(*, credentials: ResolvedAlpacaCredentials):
    """Build a stock historical-data client from ephemeral credentials."""
    from alpaca.data.historical.stock import StockHistoricalDataClient

    return StockHistoricalDataClient(
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
    )


__all__ = [
    "AlpacaSecretNames",
    "ResolvedAlpacaCredentials",
    "build_alpaca_historical_data_client",
    "build_alpaca_trading_client",
    "normalize_secret_name",
    "resolve_alpaca_credentials",
]
