"""The callers trusted to vouch for an external person (ADR 0019).

A gateway application serves people who are not Main Sequence users. A vouching caller, the
gateway itself or an application the gateway calls, names such a person in the request's W3C
Baggage (tdag-django ADR-0057) with two keys under its own segment:

- ``mainsequence.<segment>.external_user_gateway``: the gateway's workload User UID;
- ``mainsequence.<segment>.external_user_uid``: the gateway's opaque id for the person.

Main Sequence never checks those values, so the connector accepts them only from the callers
``ALPACA_CONNECTORS_VOUCHING_CALLERS`` lists, and only for the gateways each entry names. The
variable is set in the API's workflow file; unset or ``[]`` means no caller vouches. A malformed
value fails application creation, so a typo can never silently turn vouching off.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache

VOUCHING_CALLERS_ENV = "ALPACA_CONNECTORS_VOUCHING_CALLERS"
CONTEXT_PREFIX = "mainsequence."
GATEWAY_KEY_NAME = "external_user_gateway"
USER_KEY_NAME = "external_user_uid"
MAX_GATEWAY_UID_LENGTH = 64
MAX_USER_UID_LENGTH = 255

_SEGMENT = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENTRY_FIELDS = frozenset({"caller_uid", "segment", "gateway_uids"})


class VouchingCallersConfigurationError(ValueError):
    """``ALPACA_CONNECTORS_VOUCHING_CALLERS`` is not a valid allow-list."""


@dataclass(frozen=True)
class VouchingCaller:
    caller_uid: str
    segment: str
    gateway_uids: frozenset[str]

    @property
    def gateway_key(self) -> str:
        return f"{CONTEXT_PREFIX}{self.segment}.{GATEWAY_KEY_NAME}"

    @property
    def user_key(self) -> str:
        return f"{CONTEXT_PREFIX}{self.segment}.{USER_KEY_NAME}"


def _uid(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_GATEWAY_UID_LENGTH
        and value.strip() == value
        and value.isprintable()
    )


def parse_vouching_callers(raw: str | None) -> dict[str, VouchingCaller]:
    """Parse the allow-list, keyed by caller UID. Messages never quote the value."""
    if raw is None or not raw.strip():
        return {}
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VouchingCallersConfigurationError(f"{VOUCHING_CALLERS_ENV} is not JSON.") from exc
    if not isinstance(entries, list):
        raise VouchingCallersConfigurationError(f"{VOUCHING_CALLERS_ENV} must be a JSON list.")
    callers: dict[str, VouchingCaller] = {}
    for position, entry in enumerate(entries):
        where = f"{VOUCHING_CALLERS_ENV} entry {position}"
        if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
            raise VouchingCallersConfigurationError(
                f"{where} must have exactly caller_uid, segment and gateway_uids."
            )
        caller_uid, segment, gateway_uids = (
            entry["caller_uid"],
            entry["segment"],
            entry["gateway_uids"],
        )
        if not _uid(caller_uid):
            raise VouchingCallersConfigurationError(f"{where} has an invalid caller_uid.")
        if not isinstance(segment, str) or not _SEGMENT.match(segment):
            raise VouchingCallersConfigurationError(f"{where} has an invalid segment.")
        if (
            not isinstance(gateway_uids, list)
            or not gateway_uids
            or not all(_uid(gateway_uid) for gateway_uid in gateway_uids)
        ):
            raise VouchingCallersConfigurationError(f"{where} has invalid gateway_uids.")
        if caller_uid in callers:
            raise VouchingCallersConfigurationError(f"{where} repeats a caller_uid.")
        callers[caller_uid] = VouchingCaller(
            caller_uid=caller_uid,
            segment=segment,
            gateway_uids=frozenset(gateway_uids),
        )
    return callers


@lru_cache(maxsize=8)
def _cached_vouching_callers(raw: str | None) -> dict[str, VouchingCaller]:
    return parse_vouching_callers(raw)


def vouching_callers() -> dict[str, VouchingCaller]:
    """The allow-list from the process environment, parsed once per distinct value."""
    return _cached_vouching_callers(os.environ.get(VOUCHING_CALLERS_ENV))


__all__ = [
    "CONTEXT_PREFIX",
    "GATEWAY_KEY_NAME",
    "MAX_GATEWAY_UID_LENGTH",
    "MAX_USER_UID_LENGTH",
    "USER_KEY_NAME",
    "VOUCHING_CALLERS_ENV",
    "VouchingCaller",
    "VouchingCallersConfigurationError",
    "parse_vouching_callers",
    "vouching_callers",
]
