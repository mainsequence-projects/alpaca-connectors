"""One-time copy of account keys from Main Sequence Secrets into the encrypted columns (ADR 0017)."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from alpaca_connectors.account import api_key_fingerprint
from alpaca_connectors.account.credential_migration import migrate_account_credentials
from alpaca_connectors.account.credentials import (
    CREDENTIAL_SOURCE_IMPORTED,
    CREDENTIAL_SOURCE_MANAGED,
    LEGACY_SECRET_REFERENCE_COLUMNS,
)
from alpaca_connectors.account.legacy_secrets import (
    LegacySecretReferences,
    delete_legacy_managed_secrets,
    legacy_secret_references,
    read_legacy_alpaca_credentials,
)
from alpaca_connectors.platform_secrets import PlatformSecretNotFoundError

JOBS = Path(__file__).resolve().parents[1] / "src" / "jobs"
MANAGED_API_KEY = "PKMANAGED0001"
MANAGED_SECRET_KEY = "SKMANAGED0001"
EXTERNAL_API_KEY = "PKEXTERNAL0002"
EXTERNAL_SECRET_KEY = "SKEXTERNAL0002"
SECRET_VALUES = {
    "api-managed-uid": MANAGED_API_KEY,
    "secret-managed-uid": MANAGED_SECRET_KEY,
    "api-external-uid": EXTERNAL_API_KEY,
    "secret-external-uid": EXTERNAL_SECRET_KEY,
}


def managed_row(**changes):
    return {
        "account_uid": "managed-account",
        "unique_identifier": "M__ALPACA",
        "credential_source": CREDENTIAL_SOURCE_MANAGED,
        "api_key_secret_name": "ALPACA_CONNECTORS__M__ALPACA__API_KEY",
        "secret_key_secret_name": "ALPACA_CONNECTORS__M__ALPACA__SECRET_KEY",
        "api_key_secret_uid": "api-managed-uid",
        "secret_key_secret_uid": "secret-managed-uid",
        "api_key_fingerprint": api_key_fingerprint(MANAGED_API_KEY),
        "credentials_stored": False,
        **changes,
    }


def external_row(**changes):
    return {
        "account_uid": "external-account",
        "unique_identifier": "E__ALPACA_PAPER",
        "credential_source": "external",
        "api_key_secret_name": "TEAM_API_KEY",
        "secret_key_secret_name": "TEAM_SECRET_KEY",
        "api_key_secret_uid": "api-external-uid",
        "secret_key_secret_uid": "secret-external-uid",
        "api_key_fingerprint": api_key_fingerprint(EXTERNAL_API_KEY),
        "credentials_stored": False,
        **changes,
    }


def migrated_row(**changes):
    return {
        "account_uid": "new-account",
        "unique_identifier": "N__ALPACA",
        "credential_source": CREDENTIAL_SOURCE_MANAGED,
        "api_key_fingerprint": "ffff",
        "credentials_stored": True,
        **{column: None for column in LEGACY_SECRET_REFERENCE_COLUMNS},
        **changes,
    }


def read_secret(secret_uid, *, secret_name):
    return SECRET_VALUES[secret_uid]


def run_migration(rows, *, execute, read=read_secret):
    writes = []
    deletions = []
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context="context"),
        ),
        patch(
            "alpaca_connectors.account.services.list_account_registrations",
            return_value=(rows, len(rows)),
        ),
        patch("alpaca_connectors.platform_secrets.read_platform_secret_value_by_uid", read),
        patch(
            "alpaca_connectors.account.credential_migration.write_stored_credentials",
            side_effect=lambda context, *, account_uid, values: writes.append(
                (account_uid, values)
            ),
        ),
        patch(
            "alpaca_connectors.platform_secrets.delete_platform_secret",
            side_effect=lambda uid, *, secret_name: deletions.append(secret_name),
        ),
    ):
        result = migrate_account_credentials(execute=execute)
    return result, writes, deletions


def test_dry_run_reports_every_registration_and_writes_nothing() -> None:
    result, writes, deletions = run_migration(
        [managed_row(), external_row(), migrated_row()], execute=False
    )

    assert result["execute"] is False
    assert (result["migrated"], result["already_migrated"], result["blocked"]) == (2, 1, 0)
    assert writes == [] and deletions == []
    statuses = {entry["account_uid"]: entry["status"] for entry in result["accounts"]}
    assert statuses == {
        "managed-account": "migrated",
        "external-account": "migrated",
        "new-account": "already_migrated",
    }
    sources = {
        entry["account_uid"]: entry.get("new_credential_source") for entry in result["accounts"]
    }
    assert sources["managed-account"] == CREDENTIAL_SOURCE_MANAGED
    assert sources["external-account"] == CREDENTIAL_SOURCE_IMPORTED
    serialized = json.dumps(result)
    for value in SECRET_VALUES.values():
        assert value not in serialized


def test_execution_writes_sealed_keys_marks_the_row_and_deletes_managed_secrets() -> None:
    result, writes, deletions = run_migration([managed_row(), external_row()], execute=True)

    assert result["migrated"] == 2 and result["blocked"] == 0
    assert result["deleted_secrets"] == 2 and result["warnings"] == 0
    # Both values go through the model, which seals them; then the references are cleared.
    assert [account for account, _ in writes] == [
        "managed-account",
        "managed-account",
        "external-account",
        "external-account",
    ]
    managed_values, managed_clear = writes[0][1], writes[1][1]
    assert managed_values == {
        "api_key": MANAGED_API_KEY,
        "secret_key": MANAGED_SECRET_KEY,
        "credential_source": CREDENTIAL_SOURCE_MANAGED,
    }
    assert managed_clear == {column: None for column in LEGACY_SECRET_REFERENCE_COLUMNS}
    assert writes[2][1]["credential_source"] == CREDENTIAL_SOURCE_IMPORTED
    # Only the Secrets this application created are deleted; the team's are kept.
    assert deletions == [
        "ALPACA_CONNECTORS__M__ALPACA__API_KEY",
        "ALPACA_CONNECTORS__M__ALPACA__SECRET_KEY",
    ]
    serialized = json.dumps(result)
    for value in SECRET_VALUES.values():
        assert value not in serialized


def test_a_fingerprint_mismatch_blocks_the_registration_without_writing() -> None:
    result, writes, deletions = run_migration(
        [managed_row(api_key_fingerprint="someone-else")], execute=True
    )

    assert result["blocked"] == 1 and writes == [] and deletions == []
    (entry,) = result["accounts"]
    assert "fingerprint" in entry["reason"]
    assert MANAGED_API_KEY not in json.dumps(result)


def test_a_missing_secret_blocks_only_that_registration() -> None:
    def read(secret_uid, *, secret_name):
        if secret_uid == "api-external-uid":
            raise PlatformSecretNotFoundError(
                f"Main Sequence Secret {secret_name!r} does not exist."
            )
        return SECRET_VALUES[secret_uid]

    result, writes, _ = run_migration([managed_row(), external_row()], execute=True, read=read)

    assert (result["migrated"], result["blocked"]) == (1, 1)
    assert [account for account, _ in writes] == ["managed-account", "managed-account"]
    blocked = next(entry for entry in result["accounts"] if entry["status"] == "blocked")
    assert "TEAM_API_KEY" in blocked["reason"]


def test_a_rerun_retries_the_deletion_of_a_row_already_holding_its_keys() -> None:
    row = managed_row(credentials_stored=True)
    result, writes, deletions = run_migration([row], execute=True)

    assert result["cleaned"] == 1
    assert deletions == [row["api_key_secret_name"], row["secret_key_secret_name"]]
    assert writes == [
        ("managed-account", {column: None for column in LEGACY_SECRET_REFERENCE_COLUMNS})
    ]


def test_a_failed_deletion_keeps_the_references_for_the_next_run() -> None:
    def failing_delete(uid, *, secret_name):
        raise __import__(
            "alpaca_connectors.platform_secrets"
        ).platform_secrets.PlatformSecretAccessError(
            f"Main Sequence Secret {secret_name!r} could not be deleted by this runtime."
        )

    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context="context"),
        ),
        patch(
            "alpaca_connectors.account.services.list_account_registrations",
            return_value=([managed_row()], 1),
        ),
        patch("alpaca_connectors.platform_secrets.read_platform_secret_value_by_uid", read_secret),
        patch("alpaca_connectors.account.credential_migration.write_stored_credentials") as write,
        patch("alpaca_connectors.platform_secrets.delete_platform_secret", failing_delete),
    ):
        result = migrate_account_credentials(execute=True)

    assert result["migrated"] == 1 and result["warnings"] == 2
    # The keys were written once; the references were not cleared.
    assert write.call_count == 1
    assert "api_key" in write.call_args.kwargs["values"]


def test_legacy_references_read_by_uid_or_by_name_and_delete_only_managed_ones() -> None:
    assert legacy_secret_references(migrated_row()) is None
    references = legacy_secret_references(
        external_row(api_key_secret_uid=None, secret_key_secret_uid=None)
    )
    assert isinstance(references, LegacySecretReferences) and not references.managed
    with (
        patch(
            "alpaca_connectors.platform_secrets.read_platform_secret_value",
            side_effect=lambda name: {
                "TEAM_API_KEY": EXTERNAL_API_KEY,
                "TEAM_SECRET_KEY": EXTERNAL_SECRET_KEY,
            }[name],
        ) as by_name,
        patch("alpaca_connectors.platform_secrets.read_platform_secret_value_by_uid") as by_uid,
    ):
        credentials = read_legacy_alpaca_credentials(references)
    assert credentials.api_key == EXTERNAL_API_KEY
    assert by_name.call_count == 2 and by_uid.call_count == 0
    with patch("alpaca_connectors.platform_secrets.delete_platform_secret") as delete:
        assert delete_legacy_managed_secrets(references) == ([], [])
    delete.assert_not_called()


def test_the_job_checks_the_schema_first_and_prints_counts_only(monkeypatch, capsys) -> None:
    main = runpy.run_path(str(JOBS / "migrate_account_credentials.py"))["main"]
    gate_calls = []
    monkeypatch.setattr(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind",
        lambda label: gate_calls.append(label) or True,
    )
    with patch(
        "alpaca_connectors.account.credential_migration.migrate_account_credentials"
    ) as migrate:
        assert main(["--execute"]) == 0
    migrate.assert_not_called()
    assert gate_calls == ["Alpaca credential migration Job"]

    monkeypatch.setattr(
        "alpaca_connectors.migrations.schema_gate.skip_if_schema_behind", lambda label: False
    )
    summary = {
        "execute": False,
        "registrations": 2,
        "already_migrated": 1,
        "migrated": 1,
        "cleaned": 0,
        "blocked": 0,
        "deleted_secrets": 0,
        "warnings": 0,
        "accounts": [{"account_uid": "a", "status": "migrated"}],
    }
    with patch(
        "alpaca_connectors.account.credential_migration.migrate_account_credentials",
        return_value=summary,
    ) as migrate:
        assert main([]) == 0
    migrate.assert_called_once_with(execute=False)
    assert json.loads(capsys.readouterr().out) == summary

    with patch(
        "alpaca_connectors.account.credential_migration.migrate_account_credentials",
        return_value={**summary, "blocked": 1},
    ):
        assert main(["--execute"]) == 1


def test_the_launcher_starts_with_the_schema_gate() -> None:
    source = (JOBS / "migrate_account_credentials.py").read_text()
    assert source.index("skip_if_schema_behind") < source.index("migrate_account_credentials(")


@pytest.mark.parametrize("argv", [["--dry-run"], ["--execute", "extra"]])
def test_the_job_rejects_unknown_arguments(argv) -> None:
    main = runpy.run_path(str(JOBS / "migrate_account_credentials.py"))["main"]
    with pytest.raises(SystemExit):
        main(argv)
