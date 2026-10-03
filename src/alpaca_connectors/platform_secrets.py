"""Main Sequence Secret resolution and application-managed Secret writes.

Secret collection responses intentionally contain metadata only.  Resolving a value therefore
requires an exact-name metadata lookup (or a stored UID) followed by a UID-addressed detail request.

Write failures are re-raised ``from None``: an SDK exception for a create or patch request may
describe the request, and the request body carries the Secret value.
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


def upsert_platform_secret(secret_name: str, value: str) -> tuple[str, bool]:
    """Create or overwrite one Secret by exact name; return ``(uid, created)``."""
    import mainsequence.client as msc

    try:
        existing_uid: str | None = resolve_platform_secret_uid(secret_name)
    except PlatformSecretNotFoundError:
        existing_uid = None

    if existing_uid is not None:
        try:
            msc.Secret.patch_by_uid(existing_uid, value=value)
        except Exception:
            raise PlatformSecretAccessError(
                f"Main Sequence Secret {secret_name!r} could not be updated by this runtime."
            ) from None
        return existing_uid, False

    try:
        msc.Secret.create(name=secret_name, value=value)
    except Exception:
        raise PlatformSecretAccessError(
            f"Main Sequence Secret {secret_name!r} could not be created by this runtime. A "
            "Secret with this name may already exist without being visible to this runtime."
        ) from None
    # The create response may omit the UID, so read the new Secret back by its exact name.
    return resolve_platform_secret_uid(secret_name), True


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
    "upsert_platform_secret",
]
