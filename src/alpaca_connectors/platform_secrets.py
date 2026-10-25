"""Main Sequence Secret reads and deletes.

Account keys are stored on the account row since ADR 0017, so this module only serves two
transitional needs: importing keys once from Secrets a person selected by name, and the credential
migration that copies keys out of the Secrets registrations referenced before revision 0018 and
deletes the ones this application created. It is removed with the revision that drops the legacy
Secret reference columns.

Secret collection responses intentionally contain metadata only.  Resolving a value therefore
requires an exact-name metadata lookup (or a stored UID) followed by a UID-addressed detail request.
"""

from __future__ import annotations


class PlatformSecretAccessError(RuntimeError):
    """A known Secret could not be read or written by the current Main Sequence runtime."""


class PlatformSecretNotFoundError(LookupError):
    """No visible Secret has the requested exact name."""


class PlatformSecretValueMissingError(ValueError):
    """The Secret exists but its provider value is empty."""


def resolve_platform_secret_uid(secret_name: str) -> str:
    """Return the UID of the one visible Secret with this exact name."""
    import mainsequence.client as msc

    try:
        summaries = msc.Secret.filter(name=secret_name)
    except Exception as exc:
        raise PlatformSecretAccessError(
            f"Could not search Main Sequence Secrets for {secret_name!r}."
        ) from exc

    if not summaries:
        raise PlatformSecretNotFoundError(
            f"Main Sequence Secret {secret_name!r} does not exist in the current environment "
            "or is not visible to this runtime."
        )
    if len(summaries) != 1:
        raise PlatformSecretAccessError(
            f"Main Sequence Secret name {secret_name!r} is ambiguous in the current environment."
        )

    summary = summaries[0]
    if not summary.uid:
        raise PlatformSecretAccessError(
            f"Main Sequence Secret {secret_name!r} was returned without a UID."
        )
    return str(summary.uid)


def read_platform_secret_value_by_uid(secret_uid: str, *, secret_name: str) -> str:
    """Return one Secret value by UID without ever serializing or logging it.

    ``secret_name`` is the stored display name; it labels errors and is checked against the
    detail response so a reassigned UID can never silently supply another credential.
    """
    import mainsequence.client as msc

    try:
        secret = msc.Secret.get_by_uid(secret_uid)
    except Exception as exc:
        raise PlatformSecretAccessError(
            f"Main Sequence Secret {secret_name!r} exists, but this API runtime could not read "
            "its value."
        ) from exc

    if secret.name != secret_name:
        raise PlatformSecretAccessError(
            f"Main Sequence returned an unexpected Secret while resolving {secret_name!r}."
        )
    if secret.value is None:
        raise PlatformSecretValueMissingError(
            f"Main Sequence Secret {secret_name!r} exists but has no value."
        )

    value = secret.value
    resolved = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    if not resolved:
        raise PlatformSecretValueMissingError(
            f"Main Sequence Secret {secret_name!r} exists but has no value."
        )
    return resolved


def read_platform_secret_value(secret_name: str) -> str:
    """Return one Secret value by exact name without ever serializing or logging it."""
    return read_platform_secret_value_by_uid(
        resolve_platform_secret_uid(secret_name),
        secret_name=secret_name,
    )


def delete_platform_secret(secret_uid: str, *, secret_name: str) -> None:
    """Delete one Secret by UID."""
    import mainsequence.client as msc

    try:
        msc.Secret.destroy_by_uid(secret_uid)
    except Exception as exc:
        raise PlatformSecretAccessError(
            f"Main Sequence Secret {secret_name!r} could not be deleted by this runtime."
        ) from exc


__all__ = [
    "PlatformSecretAccessError",
    "PlatformSecretNotFoundError",
    "PlatformSecretValueMissingError",
    "delete_platform_secret",
    "read_platform_secret_value",
    "read_platform_secret_value_by_uid",
    "resolve_platform_secret_uid",
]
