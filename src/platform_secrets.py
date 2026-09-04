"""Main Sequence Secret value resolution through the detail endpoint.

Secret collection responses intentionally contain metadata only.  Resolving a value therefore
requires an exact-name metadata lookup followed by a UID-addressed detail request.
"""

from __future__ import annotations


class PlatformSecretAccessError(RuntimeError):
    """A known Secret could not be read by the current Main Sequence runtime."""


class PlatformSecretNotFoundError(LookupError):
    """No visible Secret has the requested exact name."""


class PlatformSecretValueMissingError(ValueError):
    """The Secret exists but its provider value is empty."""


def read_platform_secret_value(secret_name: str) -> str:
    """Return one Secret value without ever serializing or logging it."""
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

    try:
        secret = msc.Secret.get_by_uid(summary.uid)
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


__all__ = [
    "PlatformSecretAccessError",
    "PlatformSecretNotFoundError",
    "PlatformSecretValueMissingError",
    "read_platform_secret_value",
]
