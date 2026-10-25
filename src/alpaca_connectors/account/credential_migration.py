"""One-time copy of every registration's keys from Main Sequence Secrets to the encrypted columns.

Run by the ``alpaca-credential-migration`` Job (``src/jobs/migrate_account_credentials.py``) or
``alpaca-connectors account migrate-credentials`` after revision 0018 (ADR 0017). For each
registration without stored keys it reads the referenced Secrets as this workload, checks the API
key against the stored fingerprint, writes both values through the model so they are sealed, marks
the row by clearing its Secret references, and deletes the Secrets this application created. It is
idempotent: a migrated registration is skipped, and one whose managed Secrets could not be deleted
is retried. Results carry counts, names and reasons, never a key value.
"""

from __future__ import annotations

from typing import Any

from alpaca_connectors.account import api_key_fingerprint
from alpaca_connectors.account.credentials import (
    CREDENTIAL_SOURCE_IMPORTED,
    CREDENTIAL_SOURCE_MANAGED,
    LEGACY_SECRET_REFERENCE_COLUMNS,
    write_stored_credentials,
)
from alpaca_connectors.account.legacy_secrets import (
    LegacySecretReferences,
    delete_legacy_managed_secrets,
    legacy_secret_references,
    read_legacy_alpaca_credentials,
)

STATUS_ALREADY_MIGRATED = "already_migrated"
STATUS_MIGRATED = "migrated"
STATUS_CLEANED = "cleaned"
STATUS_BLOCKED = "blocked"


def _all_registrations() -> list[dict[str, Any]]:
    from alpaca_connectors.account.services import list_account_registrations

    registrations: list[dict[str, Any]] = []
    offset = 0
    while True:
        page, total = list_account_registrations(limit=100, offset=offset)
        registrations.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break
    return registrations


def _entry(registration: dict[str, Any], references: LegacySecretReferences | None) -> dict:
    entry: dict[str, Any] = {
        "account_uid": str(registration["account_uid"]),
        "unique_identifier": registration.get("unique_identifier"),
        "credential_source": registration.get("credential_source"),
    }
    if references is not None:
        entry["secret_names"] = [
            references.api_key_secret_name,
            references.secret_key_secret_name,
        ]
    return entry


def _finish_legacy_references(
    context: Any,
    *,
    account_uid: str,
    references: LegacySecretReferences,
    entry: dict[str, Any],
) -> None:
    """Delete the managed Secrets, then clear the references so a rerun skips the row."""
    deleted, warnings = delete_legacy_managed_secrets(references)
    entry["deleted_secrets"] = deleted
    entry["warnings"] = warnings
    if warnings:
        # Keep the references so the next run retries the deletion.
        return
    write_stored_credentials(
        context,
        account_uid=account_uid,
        values={column: None for column in LEGACY_SECRET_REFERENCE_COLUMNS},
    )


def migrate_account_credentials(*, execute: bool = False) -> dict[str, Any]:
    """Copy every registration's keys into the encrypted columns; dry run by default."""
    from alpaca_connectors.platform_secrets import (
        PlatformSecretAccessError,
        PlatformSecretNotFoundError,
        PlatformSecretValueMissingError,
    )
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    runtime = start_markets_engine(models=account_runtime_models())
    accounts: list[dict[str, Any]] = []
    counts = {
        STATUS_ALREADY_MIGRATED: 0,
        STATUS_MIGRATED: 0,
        STATUS_CLEANED: 0,
        STATUS_BLOCKED: 0,
    }
    deleted_secrets = 0
    warnings = 0
    for registration in _all_registrations():
        account_uid = str(registration["account_uid"])
        references = legacy_secret_references(registration)
        entry = _entry(registration, references)
        stored = bool(registration.get("credentials_stored"))
        if stored and references is None:
            entry["status"] = STATUS_ALREADY_MIGRATED
        elif stored:
            # Keys already stored (by an earlier run, or a rotation) but managed Secrets remain.
            entry["status"] = STATUS_CLEANED
            if execute:
                _finish_legacy_references(
                    runtime.context,
                    account_uid=account_uid,
                    references=references,
                    entry=entry,
                )
        else:
            if references is None:
                entry["status"] = STATUS_BLOCKED
                entry["reason"] = "The registration references no Secret; rotate its keys."
            else:
                try:
                    credentials = read_legacy_alpaca_credentials(references)
                except (
                    PlatformSecretAccessError,
                    PlatformSecretNotFoundError,
                    PlatformSecretValueMissingError,
                    ValueError,
                ) as exc:
                    entry["status"] = STATUS_BLOCKED
                    entry["reason"] = str(exc)
                else:
                    expected = str(registration.get("api_key_fingerprint") or "")
                    if api_key_fingerprint(credentials.api_key) != expected:
                        entry["status"] = STATUS_BLOCKED
                        entry["reason"] = (
                            "The API key in the referenced Secret does not match the stored "
                            "fingerprint; rotate the account's keys instead."
                        )
                    else:
                        entry["status"] = STATUS_MIGRATED
                        entry["new_credential_source"] = (
                            CREDENTIAL_SOURCE_MANAGED
                            if references.managed
                            else CREDENTIAL_SOURCE_IMPORTED
                        )
                        if execute:
                            write_stored_credentials(
                                runtime.context,
                                account_uid=account_uid,
                                values={
                                    "api_key": credentials.api_key,
                                    "secret_key": credentials.secret_key,
                                    "credential_source": entry["new_credential_source"],
                                },
                            )
                            _finish_legacy_references(
                                runtime.context,
                                account_uid=account_uid,
                                references=references,
                                entry=entry,
                            )
        counts[entry["status"]] += 1
        deleted_secrets += len(entry.get("deleted_secrets") or [])
        warnings += len(entry.get("warnings") or [])
        accounts.append(entry)
    return {
        "execute": execute,
        "registrations": len(accounts),
        **counts,
        "deleted_secrets": deleted_secrets,
        "warnings": warnings,
        "accounts": accounts,
    }


__all__ = [
    "STATUS_ALREADY_MIGRATED",
    "STATUS_BLOCKED",
    "STATUS_CLEANED",
    "STATUS_MIGRATED",
    "migrate_account_credentials",
]
