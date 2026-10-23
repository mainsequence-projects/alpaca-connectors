"""Account keys stored as application-encrypted columns (ADR 0017).

The plaintext exists only between the request and the compiled statement that seals it, and
between the row read and the Alpaca client constructor. Nothing here reaches a Main Sequence
Secret: the keyring is injected with ``metatables.encryption.set_keyring_source``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from alpaca_connectors.account import api_key_fingerprint
from alpaca_connectors.account.alpaca_account_details import AlpacaAccountDetails
from alpaca_connectors.account.credentials import (
    CREDENTIAL_SECRET_NAME,
    CREDENTIAL_SOURCE_IMPORTED,
    CREDENTIAL_SOURCE_MANAGED,
    LEGACY_SECRET_REFERENCE_COLUMNS,
    AccountCredentialsMissingError,
    AlpacaCredentialsRejectedError,
    AlpacaSecretNames,
    ResolvedAlpacaCredentials,
    StoredCredentialError,
    import_alpaca_credentials_from_secrets,
    read_alpaca_account_or_reject,
    read_registered_alpaca_credentials,
    registration_columns,
    resolve_credential_input,
    resolve_registered_alpaca_credentials,
    stored_credential_values,
    submitted_alpaca_credentials,
    write_stored_credentials,
)
from alpaca_connectors.platform_secrets import PlatformSecretAccessError
from metatables.compiled_sql.v1 import compile_sqlalchemy_statement
from metatables.encryption import (
    EncryptionError,
    column_context,
    encrypt_value,
    is_envelope,
    set_keyring_source,
)

RAW_API_KEY = "PKRAWVALUE123"
RAW_SECRET_KEY = "SKRAWVALUE456"
TABLE = AlpacaAccountDetails.__table__
WHEN = __import__("datetime").datetime(2026, 10, 24, 9, 0, tzinfo=__import__("datetime").UTC)


def offline_compile(statement, *, context, operation, dialect=None):
    """Compile as the runtime would, without a MetaTables API: the same bind processing."""
    return compile_sqlalchemy_statement(
        statement, operation=operation, data_source_uid="ds", dialect="postgresql"
    )


def seal(column: str, value: str, ring) -> str:
    return encrypt_value(value, keyring=ring, context=column_context(TABLE.name, column))


def test_the_key_columns_are_encrypted_strings_bound_to_their_column() -> None:
    for name in ("api_key", "secret_key"):
        column = TABLE.c[name]
        assert type(column.type).__name__ == "EncryptedString"
        assert column.type.secret == CREDENTIAL_SECRET_NAME == "ALPACA_CONNECTORS_DATA_KEY"
        assert column.type.context == column_context(TABLE.name, name)
        assert column.nullable
        assert "TEXT" in str(column.type.impl).upper()
        # Equality on an encrypted value is meaningless, so it is never indexed or keyed.
        assert not column.index and not column.primary_key and not column.unique


def test_registration_reads_leave_the_key_columns_out() -> None:
    keys = {column.key for column in registration_columns()}
    assert "api_key" not in keys and "secret_key" not in keys
    assert "credentials_stored" in keys
    assert "api_key_fingerprint" in keys


def test_stored_values_carry_the_fingerprint_and_clear_the_legacy_references() -> None:
    values = stored_credential_values(
        ResolvedAlpacaCredentials(RAW_API_KEY, RAW_SECRET_KEY),
        source=CREDENTIAL_SOURCE_MANAGED,
        updated_at=WHEN,
        actor_user_uid="user-uid",
    )
    assert values["api_key"] == RAW_API_KEY
    assert values["secret_key"] == RAW_SECRET_KEY
    assert values["api_key_fingerprint"] == api_key_fingerprint(RAW_API_KEY)
    assert values["credential_source"] == CREDENTIAL_SOURCE_MANAGED
    assert values["credentials_updated_by_user_uid"] == "user-uid"
    assert all(values[column] is None for column in LEGACY_SECRET_REFERENCE_COLUMNS)
    with pytest.raises(ValueError):
        stored_credential_values(
            ResolvedAlpacaCredentials(RAW_API_KEY, RAW_SECRET_KEY),
            source="external",
            updated_at=WHEN,
            actor_user_uid=None,
        )


def test_writes_leave_the_process_sealed(keyring) -> None:
    executed = []
    with (
        patch("msm.repositories.base.compile_markets_statement", offline_compile),
        patch(
            "msm.repositories.base.execute_markets_operation",
            side_effect=lambda operation, context: executed.append(operation),
        ),
    ):
        write_stored_credentials(
            SimpleNamespace(),
            account_uid="acc-uid-1",
            values=stored_credential_values(
                ResolvedAlpacaCredentials(RAW_API_KEY, RAW_SECRET_KEY),
                source=CREDENTIAL_SOURCE_IMPORTED,
                updated_at=WHEN,
                actor_user_uid=None,
            ),
        )

    (operation,) = executed
    payload = operation.model_dump(mode="json")
    serialized = str(payload)
    assert RAW_API_KEY not in serialized and RAW_SECRET_KEY not in serialized
    parameters = operation.statement.parameters
    sealed = {
        name: value
        for name, value in parameters.items()
        if name.startswith(("api_key", "secret_key"))
    }
    envelopes = [value for name, value in sealed.items() if name in ("api_key", "secret_key")]
    assert len(envelopes) == 2 and all(is_envelope(value) for value in envelopes)
    assert all(value.split(".")[1] == keyring.active for value in envelopes)
    # The same keyring opens them only in their own column.
    api_key_envelope = parameters["api_key"]
    assert TABLE.c.api_key.type.process_result_value(api_key_envelope, None) == RAW_API_KEY
    with pytest.raises(EncryptionError):
        TABLE.c.secret_key.type.process_result_value(api_key_envelope, None)
    # Clearing the legacy references travels as NULLs in the same statement.
    assert all(parameters[column] is None for column in LEGACY_SECRET_REFERENCE_COLUMNS)
    assert "UPDATE" in operation.statement.sql.upper()


def test_a_write_without_the_key_secret_fails_closed() -> None:
    def unavailable(name: str) -> str:
        raise EncryptionError(f"The Secret {name} cannot be read.", code="secret_unavailable")

    set_keyring_source(unavailable)
    try:
        with (
            patch("msm.repositories.base.compile_markets_statement", offline_compile),
            patch("msm.repositories.base.execute_markets_operation") as execute,
        ):
            with pytest.raises(EncryptionError) as raised:
                write_stored_credentials(
                    SimpleNamespace(),
                    account_uid="acc-uid-1",
                    values={"api_key": RAW_API_KEY, "secret_key": RAW_SECRET_KEY},
                )
    finally:
        set_keyring_source(None)
    execute.assert_not_called()
    assert CREDENTIAL_SECRET_NAME in str(raised.value)
    assert RAW_API_KEY not in str(raised.value)


def _read(row: dict | None):
    rows = [] if row is None else [row]
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=None),
        ),
        patch(
            "msm.repositories.base.compile_markets_statement", offline_compile
        ) as compile_statement,
        patch("msm.repositories.base.execute_markets_operation", return_value={"rows": rows}),
    ):
        return read_registered_alpaca_credentials("acc-uid-1"), compile_statement


def test_reads_open_the_sealed_keys_in_this_process(keyring) -> None:
    row = {
        "api_key": seal("api_key", RAW_API_KEY, keyring),
        "secret_key": seal("secret_key", RAW_SECRET_KEY, keyring),
    }
    credentials, _ = _read(row)
    assert credentials.api_key == RAW_API_KEY
    assert credentials.secret_key == RAW_SECRET_KEY
    assert RAW_API_KEY not in repr(credentials)
    assert resolve_registered_alpaca_credentials  # the registration-based name stays importable


def test_a_read_selects_only_the_two_key_columns(keyring) -> None:
    captured = []

    def capturing_compile(statement, *, context, operation, dialect=None):
        captured.append(statement)
        return offline_compile(statement, context=context, operation=operation)

    row = {
        "api_key": seal("api_key", RAW_API_KEY, keyring),
        "secret_key": seal("secret_key", RAW_SECRET_KEY, keyring),
    }
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=None),
        ),
        patch("msm.repositories.base.compile_markets_statement", capturing_compile),
        patch("msm.repositories.base.execute_markets_operation", return_value={"rows": [row]}),
    ):
        resolve_registered_alpaca_credentials({"account_uid": "acc-uid-1"})
    (statement,) = captured
    assert [column.name for column in statement.selected_columns] == ["api_key", "secret_key"]


def test_a_registration_without_stored_keys_points_to_the_migration(keyring) -> None:
    with pytest.raises(AccountCredentialsMissingError, match="credential migration"):
        _read({"api_key": None, "secret_key": None})
    with pytest.raises(LookupError, match="does not exist"):
        _read(None)


def test_a_stored_value_that_is_not_an_envelope_is_never_used_as_plaintext(keyring) -> None:
    with pytest.raises(StoredCredentialError) as raised:
        _read({"api_key": RAW_API_KEY, "secret_key": seal("secret_key", RAW_SECRET_KEY, keyring)})
    assert RAW_API_KEY not in str(raised.value)


def test_a_read_without_the_key_secret_names_the_secret_and_nothing_else(keyring) -> None:
    row = {
        "api_key": seal("api_key", RAW_API_KEY, keyring),
        "secret_key": seal("secret_key", RAW_SECRET_KEY, keyring),
    }

    def unavailable(name: str) -> str:
        raise EncryptionError(
            f"The Secret {name} cannot be read by this identity.", code="secret_unavailable"
        )

    set_keyring_source(unavailable)
    try:
        with pytest.raises(EncryptionError) as raised:
            _read(row)
    finally:
        set_keyring_source(None)
    assert CREDENTIAL_SECRET_NAME in str(raised.value)
    assert RAW_API_KEY not in str(raised.value)
    assert row["api_key"] not in str(raised.value)


def test_submitted_values_are_trimmed_and_validated_without_echoing_them() -> None:
    credentials = submitted_alpaca_credentials(
        api_key=f"  {RAW_API_KEY}\n", secret_key=RAW_SECRET_KEY
    )
    assert credentials.api_key == RAW_API_KEY
    assert resolve_credential_input(credentials) == (credentials, CREDENTIAL_SOURCE_MANAGED)

    for api_key, secret_key in (
        ("", RAW_SECRET_KEY),
        ("PK WITH SPACE", RAW_SECRET_KEY),
        (RAW_API_KEY, RAW_API_KEY),
        ("P" * 257, RAW_SECRET_KEY),
    ):
        with pytest.raises(ValueError) as raised:
            submitted_alpaca_credentials(api_key=api_key, secret_key=secret_key)
        assert RAW_API_KEY not in str(raised.value)
        assert RAW_SECRET_KEY not in str(raised.value)
        if api_key:
            assert api_key not in str(raised.value)


def test_importing_from_secrets_reads_by_exact_name_and_keeps_no_name() -> None:
    import mainsequence.client as msc

    names = AlpacaSecretNames(" API_KEY_NAME ", "SECRET_KEY_NAME")
    summaries = {
        "API_KEY_NAME": [SimpleNamespace(uid="api-secret-uid", value=None)],
        "SECRET_KEY_NAME": [SimpleNamespace(uid="secret-secret-uid", value=None)],
    }
    details = {
        "api-secret-uid": SimpleNamespace(name="API_KEY_NAME", value=RAW_API_KEY),
        "secret-secret-uid": SimpleNamespace(name="SECRET_KEY_NAME", value=RAW_SECRET_KEY),
    }
    with (
        patch.object(msc.Secret, "filter", side_effect=lambda **kwargs: summaries[kwargs["name"]]),
        patch.object(msc.Secret, "get_by_uid", side_effect=lambda uid: details[uid]),
    ):
        credentials, source = resolve_credential_input(names)

    assert source == CREDENTIAL_SOURCE_IMPORTED
    assert credentials == ResolvedAlpacaCredentials(RAW_API_KEY, RAW_SECRET_KEY)
    values = stored_credential_values(
        credentials, source=source, updated_at=WHEN, actor_user_uid=None
    )
    assert "API_KEY_NAME" not in str(values)


def test_importing_a_legacy_managed_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="reserved"):
        import_alpaca_credentials_from_secrets(
            AlpacaSecretNames(
                "ALPACA_CONNECTORS__010203ABCD__ALPACA__API_KEY",
                "ALPACA_CONNECTORS__010203ABCD__ALPACA__SECRET_KEY",
            )
        )


def test_an_unreadable_import_secret_is_reported_by_name() -> None:
    import mainsequence.client as msc

    with (
        patch.object(msc.Secret, "filter", return_value=[SimpleNamespace(uid="u", value=None)]),
        patch.object(msc.Secret, "get_by_uid", side_effect=RuntimeError("forbidden")),
    ):
        with pytest.raises(PlatformSecretAccessError, match="API_KEY_NAME"):
            import_alpaca_credentials_from_secrets(AlpacaSecretNames("API_KEY_NAME", "SK_NAME"))


def test_alpaca_authentication_failure_maps_to_a_fixed_error() -> None:
    from alpaca.common.exceptions import APIError

    http_error = SimpleNamespace(response=SimpleNamespace(status_code=401))
    client = MagicMock()
    client.get_account.side_effect = APIError(
        '{"message": "provider detail"}', http_error=http_error
    )
    with pytest.raises(AlpacaCredentialsRejectedError) as raised:
        read_alpaca_account_or_reject(client)
    assert str(raised.value) == "Alpaca rejected the supplied credentials."
    assert raised.value.__cause__ is None
