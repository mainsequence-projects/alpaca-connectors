"""Who owns a registered Alpaca account (ADR 0019).

An owner is a person pair ``(gateway_uid, user_uid)``. A Main Sequence user is ``(uid, uid)``. An
external person, one of a gateway application's people who is not a Main Sequence user, is
``(gateway workload User UID, the gateway's opaque person id)``; the two values always differ, so
an external person is never reported as a Main Sequence user.

The owner is set by the first registration and never changed by a rotation, a rename or an
active-state change. Registering an Alpaca account that another owner holds is refused.

Configurations run with an account's keys, so an owned account is configured only by its owner,
and an external person never claims an account without an owner while configurations that another
principal made would run on it (ADR 0022).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

OWNER_GATEWAY_COLUMN = "owner_gateway_uid"
OWNER_USER_COLUMN = "owner_user_uid"


@dataclass(frozen=True)
class AccountOwner:
    gateway_uid: str
    user_uid: str

    @classmethod
    def main_sequence_user(cls, uid: str) -> AccountOwner:
        return cls(gateway_uid=uid, user_uid=uid)

    @property
    def is_external(self) -> bool:
        """True for a person vouched for by a gateway application, not a Main Sequence user."""
        return self.gateway_uid != self.user_uid

    def column_values(self) -> dict[str, str]:
        return {OWNER_GATEWAY_COLUMN: self.gateway_uid, OWNER_USER_COLUMN: self.user_uid}


class AccountOwnedByAnotherError(Exception):
    """The Alpaca account is already registered to a different owner."""

    def __init__(self) -> None:
        # Never names the owner: the caller proves only that it holds the account's keys.
        super().__init__("This Alpaca account is registered to another owner.")


class AccountInUseError(Exception):
    """An external person's registration would claim an account that configurations run on."""

    def __init__(self) -> None:
        # Never names the configurations: another principal made them (ADR 0022).
        super().__init__(
            "This Alpaca account has no owner and enabled configurations run with its keys, so "
            "it cannot be registered for an external person until they are disabled."
        )


def registered_owner(registration: Mapping[str, Any] | None) -> AccountOwner | None:
    """The owner recorded on a registration row, or None when it has none."""
    if not registration:
        return None
    gateway_uid = registration.get(OWNER_GATEWAY_COLUMN)
    user_uid = registration.get(OWNER_USER_COLUMN)
    if not gateway_uid or not user_uid:
        return None
    return AccountOwner(gateway_uid=str(gateway_uid), user_uid=str(user_uid))


def require_owner_may_register(
    registration: Mapping[str, Any] | None,
    owner: AccountOwner,
) -> None:
    """Refuse a registration that would take an account away from its recorded owner.

    No row and a row without an owner are both claimed by ``owner``; the same owner registers
    again as before.
    """
    current = registered_owner(registration)
    if current is not None and current != owner:
        raise AccountOwnedByAnotherError()


def require_owner_may_configure(
    registration: Mapping[str, Any] | None,
    owner: AccountOwner,
) -> None:
    """Refuse a configuration that would run with another owner's keys (ADR 0022).

    An account without an owner stays open to every principal, as before; a missing one is left
    for the configuration service to report.
    """
    require_owner_may_register(registration, owner)


def is_claim_by_external_person(
    registration: Mapping[str, Any] | None,
    owner: AccountOwner | None,
) -> bool:
    """Whether a registration would make an external person the owner of an unowned account."""
    return owner is not None and owner.is_external and registered_owner(registration) is None


__all__ = [
    "OWNER_GATEWAY_COLUMN",
    "OWNER_USER_COLUMN",
    "AccountInUseError",
    "AccountOwnedByAnotherError",
    "AccountOwner",
    "is_claim_by_external_person",
    "registered_owner",
    "require_owner_may_configure",
    "require_owner_may_register",
]
