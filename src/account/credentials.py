"""Alpaca credential references and application-managed credential Secrets.

An account registration references exactly two Main Sequence Secrets (ADR 0011):

- ``managed`` Secrets are created by this application from values a user submitted, use the
  deterministic ``ALPACA_CONNECTORS__<Account.unique_identifier>__...`` names, and follow the
  registration's lifecycle;
- ``external`` Secrets were selected by name and are only ever read.

Values are resolved only at the provider boundary, kept in memory for the client constructor, and
never returned or persisted outside Main Sequence Secrets.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.platform_secrets import (
    PlatformSecretAccessError,
    delete_platform_secret,
    read_platform_secret_value_by_uid,
    resolve_platform_secret_uid,
    upsert_platform_secret,
)

logger = logging.getLogger(__name__)

CREDENTIAL_SOURCE_MANAGED = "managed"
CREDENTIAL_SOURCE_EXTERNAL = "external"
CREDENTIAL_SOURCES = (CREDENTIAL_SOURCE_MANAGED, CREDENTIAL_SOURCE_EXTERNAL)

# Reserved for Secrets this application creates; never used for a user-selected Secret.
MANAGED_SECRET_PREFIX = "ALPACA_CONNECTORS__"

_MAX_CREDENTIAL_VALUE_LENGTH = 256


class AlpacaCredentialsRejectedError(ValueError):
    """Alpaca rejected the supplied credentials.  The message never echoes provider text."""

    def __init__(self) -> None:
        super().__init__("Alpaca rejected the supplied credentials.")


class AccountSecretReferenceError(ValueError):
    """A registration does not carry the Secret UIDs required to resolve its credentials."""


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
    """Names of the two Main Sequence Secrets used by an Alpaca account."""

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


@dataclass(frozen=True, slots=True)
class AlpacaSecretReferences:
    """Durable references to the two Secrets an account registration resolves."""

    api_key_secret_name: str
    secret_key_secret_name: str
    api_key_secret_uid: str
    secret_key_secret_uid: str
    credential_source: str

    def detail_values(self) -> dict[str, str]:
        """``AlpacaAccountDetails`` column values for these references."""
        return {
            "api_key_secret_name": self.api_key_secret_name,
            "secret_key_secret_name": self.secret_key_secret_name,
            "api_key_secret_uid": self.api_key_secret_uid,
            "secret_key_secret_uid": self.secret_key_secret_uid,
            "credential_source": self.credential_source,
        }


# Registration and plan inputs: existing Secrets by name, or values to store as managed Secrets.
AlpacaCredentialInput = AlpacaSecretNames | ResolvedAlpacaCredentials


def submitted_alpaca_credentials(*, api_key: str, secret_key: str) -> ResolvedAlpacaCredentials:
    """Validate credential values a user submitted.  Errors never include the values."""
    values: dict[str, str] = {}
    for field_name, raw_value in (("api_key", api_key), ("secret_key", secret_key)):
        value = (raw_value or "").strip()
        if not value:
            raise ValueError(f"{field_name} must be a non-empty Alpaca credential.")
        if len(value) > _MAX_CREDENTIAL_VALUE_LENGTH:
            raise ValueError(
                f"{field_name} must be at most {_MAX_CREDENTIAL_VALUE_LENGTH} characters."
            )
        if any(character.isspace() for character in value):
            raise ValueError(f"{field_name} must not contain whitespace.")
        values[field_name] = value
    if values["api_key"] == values["secret_key"]:
        raise ValueError("api_key and secret_key must be different values.")
    return ResolvedAlpacaCredentials(api_key=values["api_key"], secret_key=values["secret_key"])


def managed_secret_names(account_unique_identifier: str) -> AlpacaSecretNames:
    """Deterministic names of the managed Secrets for one Account identity."""
    identifier = (account_unique_identifier or "").strip()
    if not identifier:
        raise ValueError("account_unique_identifier must be a non-empty string.")
    return AlpacaSecretNames(
        api_key_secret_name=f"{MANAGED_SECRET_PREFIX}{identifier}__API_KEY",
        secret_key_secret_name=f"{MANAGED_SECRET_PREFIX}{identifier}__SECRET_KEY",
    )


def resolve_alpaca_secret_references(secret_names: AlpacaSecretNames) -> AlpacaSecretReferences:
    """Resolve user-selected (external) Secret names to their UIDs."""
    for name in (secret_names.api_key_secret_name, secret_names.secret_key_secret_name):
        if name.startswith(MANAGED_SECRET_PREFIX):
            # A managed Secret's lifecycle belongs to its own registration; referencing it as
            # external would let a later rotation or removal break this account.
            raise ValueError(
                f"Secret {name!r} uses the reserved {MANAGED_SECRET_PREFIX!r} prefix; select a "
                "Secret that this application does not manage."
            )
    return AlpacaSecretReferences(
        api_key_secret_name=secret_names.api_key_secret_name,
        secret_key_secret_name=secret_names.secret_key_secret_name,
        api_key_secret_uid=resolve_platform_secret_uid(secret_names.api_key_secret_name),
        secret_key_secret_uid=resolve_platform_secret_uid(secret_names.secret_key_secret_name),
        credential_source=CREDENTIAL_SOURCE_EXTERNAL,
    )


def registered_secret_references(registration: Mapping[str, Any]) -> AlpacaSecretReferences:
    """Return the stored Secret references of one account registration."""
    api_key_secret_uid = registration.get("api_key_secret_uid")
    secret_key_secret_uid = registration.get("secret_key_secret_uid")
    if not api_key_secret_uid or not secret_key_secret_uid:
        raise AccountSecretReferenceError(
            f"Alpaca account registration {registration.get('account_uid')!s} has no stored "
            "Secret UIDs. Run `alpaca-connectors account backfill-secret-uids --execute`."
        )
    return AlpacaSecretReferences(
        api_key_secret_name=str(registration["api_key_secret_name"]),
        secret_key_secret_name=str(registration["secret_key_secret_name"]),
        api_key_secret_uid=str(api_key_secret_uid),
        secret_key_secret_uid=str(secret_key_secret_uid),
        credential_source=str(registration.get("credential_source") or CREDENTIAL_SOURCE_EXTERNAL),
    )


def resolve_alpaca_credentials(references: AlpacaSecretReferences) -> ResolvedAlpacaCredentials:
    """Resolve both referenced Secrets immediately before constructing an Alpaca client."""
    return ResolvedAlpacaCredentials(
        api_key=read_platform_secret_value_by_uid(
            references.api_key_secret_uid,
            secret_name=references.api_key_secret_name,
        ),
        secret_key=read_platform_secret_value_by_uid(
            references.secret_key_secret_uid,
            secret_name=references.secret_key_secret_name,
        ),
    )


def resolve_registered_alpaca_credentials(
    registration: Mapping[str, Any],
) -> ResolvedAlpacaCredentials:
    """Resolve the credentials of one stored account registration by Secret UID."""
    return resolve_alpaca_credentials(registered_secret_references(registration))


@dataclass(frozen=True, slots=True)
class ManagedSecretWrite:
    """Result of storing managed credentials: the references and the Secrets newly created."""

    references: AlpacaSecretReferences
    created: tuple[tuple[str, str], ...]  # (uid, name) pairs created by this write


def plan_managed_secret_writes(account_unique_identifier: str) -> list[dict[str, str]]:
    """Read-only: report whether each managed Secret would be created or updated."""
    from src.platform_secrets import PlatformSecretNotFoundError

    names = managed_secret_names(account_unique_identifier)
    writes = []
    for name in (names.api_key_secret_name, names.secret_key_secret_name):
        try:
            resolve_platform_secret_uid(name)
            action = "update"
        except PlatformSecretNotFoundError:
            action = "create"
        writes.append({"name": name, "action": action})
    return writes


def store_managed_alpaca_credentials(
    account_unique_identifier: str,
    credentials: ResolvedAlpacaCredentials,
) -> ManagedSecretWrite:
    """Create or overwrite the two managed Secrets for one Account identity."""
    names = managed_secret_names(account_unique_identifier)
    created: list[tuple[str, str]] = []
    try:
        api_key_uid, api_key_created = upsert_platform_secret(
            names.api_key_secret_name,
            credentials.api_key,
        )
        if api_key_created:
            created.append((api_key_uid, names.api_key_secret_name))
        secret_key_uid, secret_key_created = upsert_platform_secret(
            names.secret_key_secret_name,
            credentials.secret_key,
        )
        if secret_key_created:
            created.append((secret_key_uid, names.secret_key_secret_name))
    except Exception:
        delete_secrets_best_effort(created)
        raise
    return ManagedSecretWrite(
        references=AlpacaSecretReferences(
            api_key_secret_name=names.api_key_secret_name,
            secret_key_secret_name=names.secret_key_secret_name,
            api_key_secret_uid=api_key_uid,
            secret_key_secret_uid=secret_key_uid,
            credential_source=CREDENTIAL_SOURCE_MANAGED,
        ),
        created=tuple(created),
    )


def delete_secrets_best_effort(
    secrets: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> tuple[list[str], list[str]]:
    """Delete ``(uid, name)`` managed Secrets; return ``(deleted_names, warnings)``."""
    deleted = []
    warnings = []
    for secret_uid, secret_name in secrets:
        if not secret_name.startswith(MANAGED_SECRET_PREFIX):
            # Defence in depth: only application-managed Secrets are ever deleted.
            warnings.append(f"Secret {secret_name!r} is not application-managed and was kept.")
            continue
        try:
            delete_platform_secret(secret_uid, secret_name=secret_name)
        except PlatformSecretAccessError:
            logger.warning("Could not delete managed Main Sequence Secret %s", secret_name)
            warnings.append(f"Managed Secret {secret_name!r} could not be deleted.")
        else:
            deleted.append(secret_name)
    return deleted, warnings


def delete_managed_alpaca_secrets(
    references: AlpacaSecretReferences,
) -> tuple[list[str], list[str]]:
    """Delete a registration's managed Secrets; external Secrets are never deleted.

    Returns ``(deleted_names, warnings)``.
    """
    if references.credential_source != CREDENTIAL_SOURCE_MANAGED:
        return [], []
    return delete_secrets_best_effort(
        [
            (references.api_key_secret_uid, references.api_key_secret_name),
            (references.secret_key_secret_uid, references.secret_key_secret_name),
        ]
    )


def read_alpaca_account_or_reject(client: Any) -> Any:
    """Read the Alpaca account, mapping an authentication failure to a fixed error."""
    from alpaca.common.exceptions import APIError

    try:
        return client.get_account()
    except APIError as exc:
        if exc.status_code in (401, 403):
            raise AlpacaCredentialsRejectedError() from None
        raise


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
    "CREDENTIAL_SOURCES",
    "CREDENTIAL_SOURCE_EXTERNAL",
    "CREDENTIAL_SOURCE_MANAGED",
    "MANAGED_SECRET_PREFIX",
    "AccountSecretReferenceError",
    "AlpacaCredentialInput",
    "AlpacaCredentialsRejectedError",
    "AlpacaSecretNames",
    "AlpacaSecretReferences",
    "ManagedSecretWrite",
    "ResolvedAlpacaCredentials",
    "build_alpaca_historical_data_client",
    "build_alpaca_trading_client",
    "delete_managed_alpaca_secrets",
    "delete_secrets_best_effort",
    "managed_secret_names",
    "normalize_secret_name",
    "plan_managed_secret_writes",
    "read_alpaca_account_or_reject",
    "registered_secret_references",
    "resolve_alpaca_credentials",
    "resolve_alpaca_secret_references",
    "resolve_registered_alpaca_credentials",
    "store_managed_alpaca_credentials",
    "submitted_alpaca_credentials",
]
