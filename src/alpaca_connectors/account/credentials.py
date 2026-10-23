"""Alpaca account credentials, stored as application-encrypted columns (ADR 0017).

An account registration carries its Alpaca API key and secret key on ``AlpacaAccountDetails`` in
the ``api_key`` and ``secret_key`` columns, which are ``metatables.EncryptedString`` columns
sealed in this process under the ``ALPACA_CONNECTORS_DATA_KEY`` Secret (MetaTables ADR 0025). A
value is accepted from a person (``managed``) or copied once from Main Sequence Secrets selected
by name (``imported``); either way the plaintext exists only in process memory, between the
request and the compiled statement that encrypts it, and again between the row read and the
Alpaca client constructor.

Reading needs the Secret: a process without it fails with the MetaTables error that names the
Secret. There is no plaintext fallback, and a stored value that is not an envelope is refused.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from alpaca_connectors.settings import CREDENTIAL_SECRET_NAME

logger = logging.getLogger(__name__)

# Keys a person entered through the API or the site.
CREDENTIAL_SOURCE_MANAGED = "managed"
# Keys copied once from Main Sequence Secrets selected by name (CLI, Agent, or the migration).
CREDENTIAL_SOURCE_IMPORTED = "imported"
# Transition only: a registration made before revision 0018 whose keys still live in Secrets.
CREDENTIAL_SOURCE_EXTERNAL = "external"
CREDENTIAL_SOURCES = (
    CREDENTIAL_SOURCE_MANAGED,
    CREDENTIAL_SOURCE_IMPORTED,
    CREDENTIAL_SOURCE_EXTERNAL,
)

# Names of the Secrets this application created before ADR 0017; never a valid import source.
MANAGED_SECRET_PREFIX = "ALPACA_CONNECTORS__"

# The encrypted columns, and the transition columns the first stored keys clear.
STORED_CREDENTIAL_COLUMNS = ("api_key", "secret_key")
LEGACY_SECRET_REFERENCE_COLUMNS = (
    "api_key_secret_name",
    "secret_key_secret_name",
    "api_key_secret_uid",
    "secret_key_secret_uid",
)

_MAX_CREDENTIAL_VALUE_LENGTH = 256


class AlpacaCredentialsRejectedError(ValueError):
    """Alpaca rejected the supplied credentials.  The message never echoes provider text."""

    def __init__(self) -> None:
        super().__init__("Alpaca rejected the supplied credentials.")


class AccountCredentialsMissingError(LookupError):
    """A registration has no stored keys: made before revision 0018 and not migrated yet."""

    def __init__(self, account_uid: str) -> None:
        super().__init__(
            f"Alpaca account registration {account_uid!s} has no stored keys yet. Run the "
            "Alpaca credential migration Job, or rotate the account's keys."
        )


class StoredCredentialError(RuntimeError):
    """A stored key column holds something this application did not seal; it is never used."""


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
    """Names of two Main Sequence Secrets to import an account's keys from, once."""

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


# Registration and plan inputs: values a person submitted, or Secrets to import them from.
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


def import_alpaca_credentials_from_secrets(names: AlpacaSecretNames) -> ResolvedAlpacaCredentials:
    """Read both named Secrets once, as this workload; the names are never stored."""
    from alpaca_connectors.platform_secrets import read_platform_secret_value

    for name in (names.api_key_secret_name, names.secret_key_secret_name):
        if name.startswith(MANAGED_SECRET_PREFIX):
            # Secrets this application created before ADR 0017 belong to the registration the
            # credential migration copies them into; importing one elsewhere would copy a key
            # the migration then deletes.
            raise ValueError(
                f"Secret {name!r} uses the reserved {MANAGED_SECRET_PREFIX!r} prefix; it belongs "
                "to the registration the credential migration copies it into."
            )
    return submitted_alpaca_credentials(
        api_key=read_platform_secret_value(names.api_key_secret_name),
        secret_key=read_platform_secret_value(names.secret_key_secret_name),
    )


def resolve_credential_input(
    credentials: AlpacaCredentialInput,
) -> tuple[ResolvedAlpacaCredentials, str]:
    """Return usable values and the ``credential_source`` they are recorded with."""
    if isinstance(credentials, AlpacaSecretNames):
        return import_alpaca_credentials_from_secrets(credentials), CREDENTIAL_SOURCE_IMPORTED
    return credentials, CREDENTIAL_SOURCE_MANAGED


def stored_credential_values(
    credentials: ResolvedAlpacaCredentials,
    *,
    source: str,
    updated_at: dt.datetime,
    actor_user_uid: str | None,
) -> dict[str, Any]:
    """``AlpacaAccountDetails`` column values that store these keys.

    The values are plaintext here and are encrypted by the model's column type when the
    statement that carries them is compiled; the transition Secret references end with them.
    """
    from alpaca_connectors.account import api_key_fingerprint

    if source not in (CREDENTIAL_SOURCE_MANAGED, CREDENTIAL_SOURCE_IMPORTED):
        raise ValueError(f"Unsupported credential source {source!r}.")
    return {
        "api_key": credentials.api_key,
        "secret_key": credentials.secret_key,
        "api_key_fingerprint": api_key_fingerprint(credentials.api_key),
        "credential_source": source,
        "credentials_updated_at": updated_at,
        "credentials_updated_by_user_uid": actor_user_uid,
        **{column: None for column in LEGACY_SECRET_REFERENCE_COLUMNS},
    }


def registration_columns() -> list[Any]:
    """The detail columns a registration read returns: every column except the encrypted ones.

    The read-only ``credentials_stored`` flag reports whether the keys are stored, without
    carrying them.
    """
    from alpaca_connectors.account.alpaca_account_details import AlpacaAccountDetails
    from metatables import EncryptedString

    table = AlpacaAccountDetails.__table__
    columns = [
        column.label(column.key)
        for column in table.columns
        if not isinstance(column.type, EncryptedString)
    ]
    columns.append(
        (table.c.api_key.isnot(None) & table.c.secret_key.isnot(None)).label("credentials_stored")
    )
    return columns


def write_stored_credentials(
    context: Any,
    *,
    account_uid: str,
    values: Mapping[str, Any],
) -> None:
    """Update one registration's credential columns through the model, so values are sealed.

    Unlike ``update_model`` this keeps ``None`` values, which is how the transition Secret
    references are cleared.
    """
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import update

    from alpaca_connectors.account.alpaca_account_details import AlpacaAccountDetails

    table = AlpacaAccountDetails.__table__
    statement = (
        update(AlpacaAccountDetails)
        .where(table.c.account_uid == account_uid)
        .values({getattr(AlpacaAccountDetails, key): value for key, value in values.items()})
    )
    execute_markets_operation(
        compile_markets_statement(statement, context=context, operation="update"),
        context=context,
    )


def _open_stored_value(column: Any, value: Any, *, account_uid: str) -> str:
    from metatables.encryption import is_envelope

    if value is None:
        raise AccountCredentialsMissingError(account_uid)
    if not is_envelope(value):
        raise StoredCredentialError(
            f"Alpaca account registration {account_uid!s} holds a {column.name} value that this "
            "application did not encrypt; it is not used. Rotate the account's keys."
        )
    opened = column.type.process_result_value(value, None)
    if not isinstance(opened, str) or not opened:
        raise StoredCredentialError(
            f"Alpaca account registration {account_uid!s} holds an empty {column.name}."
        )
    return opened


def read_registered_alpaca_credentials(account_uid: str) -> ResolvedAlpacaCredentials:
    """Read one registration's sealed keys and open them in this process.

    The compiled select carries the envelopes back; the column type opens them with the keyring
    the ``ALPACA_CONNECTORS_DATA_KEY`` Secret holds. A process that cannot read that Secret fails
    with ``metatables.EncryptionError``, which names the Secret and nothing else.
    """
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from alpaca_connectors.account.alpaca_account_details import AlpacaAccountDetails
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    runtime = start_markets_engine(models=account_runtime_models())
    table = AlpacaAccountDetails.__table__
    statement = (
        select(table.c.api_key, table.c.secret_key)
        .where(table.c.account_uid == account_uid)
        .limit(1)
    )
    rows = operation_result_rows(
        execute_markets_operation(
            compile_markets_statement(statement, context=runtime.context, operation="select"),
            context=runtime.context,
        )
    )
    if not rows:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    row = rows[0]
    return ResolvedAlpacaCredentials(
        api_key=_open_stored_value(table.c.api_key, row.get("api_key"), account_uid=account_uid),
        secret_key=_open_stored_value(
            table.c.secret_key, row.get("secret_key"), account_uid=account_uid
        ),
    )


def resolve_registered_alpaca_credentials(
    registration: Mapping[str, Any],
) -> ResolvedAlpacaCredentials:
    """Open the stored keys of one account registration immediately before building a client."""
    return read_registered_alpaca_credentials(str(registration["account_uid"]))


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
    "CREDENTIAL_SECRET_NAME",
    "CREDENTIAL_SOURCES",
    "CREDENTIAL_SOURCE_EXTERNAL",
    "CREDENTIAL_SOURCE_IMPORTED",
    "CREDENTIAL_SOURCE_MANAGED",
    "LEGACY_SECRET_REFERENCE_COLUMNS",
    "MANAGED_SECRET_PREFIX",
    "STORED_CREDENTIAL_COLUMNS",
    "AccountCredentialsMissingError",
    "AlpacaCredentialInput",
    "AlpacaCredentialsRejectedError",
    "AlpacaSecretNames",
    "ResolvedAlpacaCredentials",
    "StoredCredentialError",
    "build_alpaca_historical_data_client",
    "build_alpaca_trading_client",
    "import_alpaca_credentials_from_secrets",
    "normalize_secret_name",
    "read_alpaca_account_or_reject",
    "read_registered_alpaca_credentials",
    "registration_columns",
    "resolve_credential_input",
    "resolve_registered_alpaca_credentials",
    "stored_credential_values",
    "submitted_alpaca_credentials",
    "write_stored_credentials",
]
