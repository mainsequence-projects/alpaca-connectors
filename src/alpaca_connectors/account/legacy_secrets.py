"""Main Sequence Secrets of registrations made before ADR 0017 (transition only).

Before revision 0018 an account registration referenced two Secrets by name and UID (ADR 0011):
``managed`` ones this application created under the ``ALPACA_CONNECTORS__`` prefix, or
``external`` ones a person selected. The credential migration copies their values into the
encrypted columns and deletes the managed ones; this module is what it, and the rotation and
removal of a not-yet-migrated registration, use. It is removed with the revision that drops the
legacy reference columns.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from alpaca_connectors.account.credentials import (
    CREDENTIAL_SOURCE_MANAGED,
    MANAGED_SECRET_PREFIX,
    ResolvedAlpacaCredentials,
    submitted_alpaca_credentials,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LegacySecretReferences:
    """The Secret references a registration still carries from before ADR 0017."""

    api_key_secret_name: str
    secret_key_secret_name: str
    api_key_secret_uid: str | None
    secret_key_secret_uid: str | None
    credential_source: str

    @property
    def managed(self) -> bool:
        return self.credential_source == CREDENTIAL_SOURCE_MANAGED


def legacy_secret_references(registration: Mapping[str, Any]) -> LegacySecretReferences | None:
    """Return the Secret references a registration still carries, or ``None`` once migrated."""
    api_key_secret_name = registration.get("api_key_secret_name")
    secret_key_secret_name = registration.get("secret_key_secret_name")
    if not api_key_secret_name or not secret_key_secret_name:
        return None
    return LegacySecretReferences(
        api_key_secret_name=str(api_key_secret_name),
        secret_key_secret_name=str(secret_key_secret_name),
        api_key_secret_uid=registration.get("api_key_secret_uid") or None,
        secret_key_secret_uid=registration.get("secret_key_secret_uid") or None,
        credential_source=str(registration.get("credential_source") or "external"),
    )


def _read(secret_name: str, secret_uid: str | None) -> str:
    from alpaca_connectors.platform_secrets import (
        read_platform_secret_value,
        read_platform_secret_value_by_uid,
    )

    if secret_uid:
        return read_platform_secret_value_by_uid(secret_uid, secret_name=secret_name)
    # Registrations that predate UID storage (ADR 0011) and were never backfilled.
    return read_platform_secret_value(secret_name)


def read_legacy_alpaca_credentials(
    references: LegacySecretReferences,
) -> ResolvedAlpacaCredentials:
    """Read both referenced Secrets, by UID when stored and by exact name otherwise."""
    return submitted_alpaca_credentials(
        api_key=_read(references.api_key_secret_name, references.api_key_secret_uid),
        secret_key=_read(references.secret_key_secret_name, references.secret_key_secret_uid),
    )


def delete_legacy_managed_secrets(
    references: LegacySecretReferences,
) -> tuple[list[str], list[str]]:
    """Delete the managed Secrets a registration still references; external ones are kept.

    Returns ``(deleted_names, warnings)``. Only Secrets with the reserved prefix are ever deleted,
    and a failure is reported by name and never raised: the registration itself is already
    correct, and the next migration run retries.
    """
    from alpaca_connectors.platform_secrets import (
        PlatformSecretAccessError,
        PlatformSecretNotFoundError,
        delete_platform_secret,
        resolve_platform_secret_uid,
    )

    if not references.managed:
        return [], []
    deleted: list[str] = []
    warnings: list[str] = []
    for secret_uid, secret_name in (
        (references.api_key_secret_uid, references.api_key_secret_name),
        (references.secret_key_secret_uid, references.secret_key_secret_name),
    ):
        if not secret_name.startswith(MANAGED_SECRET_PREFIX):
            # Defence in depth: only application-managed Secrets are ever deleted.
            warnings.append(f"Secret {secret_name!r} is not application-managed and was kept.")
            continue
        try:
            delete_platform_secret(
                secret_uid or resolve_platform_secret_uid(secret_name),
                secret_name=secret_name,
            )
        except PlatformSecretNotFoundError:
            # Already gone: an earlier run deleted it before the row was marked.
            deleted.append(secret_name)
        except PlatformSecretAccessError:
            logger.warning("Could not delete managed Main Sequence Secret %s", secret_name)
            warnings.append(f"Managed Secret {secret_name!r} could not be deleted.")
        else:
            deleted.append(secret_name)
    return deleted, warnings


__all__ = [
    "LegacySecretReferences",
    "delete_legacy_managed_secrets",
    "legacy_secret_references",
    "read_legacy_alpaca_credentials",
]
