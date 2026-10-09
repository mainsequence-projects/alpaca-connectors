"""Requests for a person to enter Alpaca keys on the Alpaca site.

The Agent never receives Alpaca key values. When a person asks it to register an account, or to
replace an account's keys, the API stores one of these requests for that person: the requester the
platform names on the Agent's call. The Alpaca site lists the signed-in person's own pending
requests; the person types the keys there and the site submits them to the API together with the
request UID. The API accepts the submission only from that same person, while the request is
pending and unexpired, and then completes the request with the account UID the Agent reads back.

A request stores no credential value, Secret name, or Secret UID.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar, Literal

from msm.api.base import MarketsMetaTableRow
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from pydantic import ConfigDict
from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from alpaca_connectors.account import ALPACA_ENVIRONMENTS
from alpaca_connectors.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin

KeyRequestAction = Literal["register", "rotate"]
KeyRequestStatus = Literal["pending", "completed", "cancelled", "expired"]

UTC = dt.timezone.utc
# How long the person has to enter the keys on the site.
KEY_REQUEST_TTL = dt.timedelta(hours=1)
KEY_REQUEST_ACTIONS: tuple[KeyRequestAction, ...] = ("register", "rotate")


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class AccountKeyRequestTable(ProjectStorageNameMixin, AlpacaMarketsMetaTableMixin, MarketsBase):
    """One request for one person to enter Alpaca keys for a new or an existing account."""

    __project_storage_concept__ = "account_key_request"
    __markets_base_identifier__ = "AccountKeyRequest"
    __metatable_description__ = (
        "One row per request for a person to enter Alpaca API keys on the Alpaca Connectors "
        "site. Requests to register a new Alpaca brokerage account or replace a registered "
        "account's API keys, started by the Alpaca Agent for the person it serves, with their "
        "status, expiry and resulting account; key values are never stored."
    )
    __metatable_labels__ = ["brokerage", "accounts", "operations", "alpaca"]
    __table_args__ = markets_table_args(
        "AccountKeyRequest",
        Index(None, "requester_uid"),
        Index(None, "status"),
        Index(None, "expires_at"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={
            "label": "Request UID",
            "description": "Public UUID the Alpaca Agent and the Alpaca site use for the request.",
        },
    )
    requester_uid: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={
            "label": "Requester UID",
            "description": (
                "Main Sequence user UID of the person the request belongs to; only this person "
                "may submit its keys."
            ),
        },
    )
    action: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Action",
            "description": "register for a new account, or rotate to replace a registered account's keys.",
        },
    )
    account_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        info={
            "label": "Account name",
            "description": "Display name requested for a new account; null when none was requested.",
        },
    )
    environment: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        info={
            "label": "Environment",
            "description": "Alpaca trading environment the keys must belong to: paper or live.",
        },
    )
    account_uid: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        info={
            "label": "Account UID",
            "description": (
                "Main Sequence account UID: the account a rotation replaces keys for, or the "
                "account a completed registration created; null for a registration not yet "
                "completed."
            ),
        },
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Status",
            "description": "pending, completed or cancelled; a pending request past its expiry is expired.",
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Created at", "description": "Time the request was created, in UTC."},
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        info={
            "label": "Expires at",
            "description": "Time, in UTC, after which keys are no longer accepted for the request.",
        },
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Completed at",
            "description": (
                "Time, in UTC, the keys were accepted or the request was cancelled; null while "
                "pending."
            ),
        },
    )


class AccountKeyRequest(MarketsMetaTableRow):
    """Typed row operations for ``AccountKeyRequestTable``."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AccountKeyRequestTable]] = AccountKeyRequestTable
    __required_tables__: ClassVar[list[type[AccountKeyRequestTable]]] = [AccountKeyRequestTable]

    requester_uid: str
    action: KeyRequestAction
    account_name: str | None
    environment: Literal["paper", "live"]
    account_uid: str | None
    status: Literal["pending", "completed", "cancelled"]
    created_at: dt.datetime
    expires_at: dt.datetime
    completed_at: dt.datetime | None


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def effective_status(
    request: AccountKeyRequest, *, now: dt.datetime | None = None
) -> KeyRequestStatus:
    """The request's status, with a pending request past its expiry reported as expired."""
    if request.status == "pending" and _aware(request.expires_at) <= (now or utc_now()):
        return "expired"
    return request.status


def describe_account_key_request(
    request: AccountKeyRequest,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Public view of a request for the Agent and the site; it holds no credential reference."""
    return {
        "uid": str(request.uid),
        "action": request.action,
        "account_name": request.account_name,
        "environment": request.environment,
        "account_uid": request.account_uid,
        "status": effective_status(request, now=now),
        "created_at": _aware(request.created_at).isoformat(),
        "expires_at": _aware(request.expires_at).isoformat(),
        "completed_at": _aware(request.completed_at).isoformat() if request.completed_at else None,
    }


def _start_engine() -> None:
    from alpaca_connectors.runtime import start_markets_engine

    start_markets_engine()


def create_account_key_request(
    *,
    requester_uid: str,
    action: KeyRequestAction,
    environment: str,
    account_name: str | None = None,
    account_uid: str | None = None,
    now: dt.datetime | None = None,
) -> AccountKeyRequest:
    """Store a pending request for ``requester_uid`` to enter keys on the Alpaca site."""
    if not requester_uid:
        raise ValueError("A key request needs the person it belongs to.")
    if action not in KEY_REQUEST_ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(KEY_REQUEST_ACTIONS)}.")
    if environment not in ALPACA_ENVIRONMENTS:
        raise ValueError(f"environment must be one of: {', '.join(ALPACA_ENVIRONMENTS)}.")
    if action == "rotate" and not account_uid:
        raise ValueError("A key rotation request needs the account whose keys it replaces.")
    if action == "register" and account_uid:
        raise ValueError("A registration request cannot name an existing account.")
    created_at = now or utc_now()
    _start_engine()
    return AccountKeyRequest.create(
        {
            "uid": new_markets_uid(),
            "requester_uid": requester_uid,
            "action": action,
            "account_name": account_name,
            "environment": environment,
            "account_uid": account_uid,
            "status": "pending",
            "created_at": created_at,
            "expires_at": created_at + KEY_REQUEST_TTL,
            "completed_at": None,
        }
    )


def get_account_key_request(request_uid: uuid.UUID | str) -> AccountKeyRequest | None:
    _start_engine()
    return AccountKeyRequest.get_by_uid(request_uid)


def get_own_account_key_request(
    request_uid: uuid.UUID | str,
    *,
    requester_uid: str,
) -> AccountKeyRequest:
    """Return the request when it belongs to ``requester_uid``.

    Another person's request is reported exactly like a missing one, so a UID reveals nothing.
    """
    request = get_account_key_request(request_uid)
    if request is None or request.requester_uid != requester_uid:
        raise LookupError(f"Account key request {request_uid!s} does not exist.")
    return request


def list_pending_account_key_requests(
    requester_uid: str,
    *,
    now: dt.datetime | None = None,
) -> list[AccountKeyRequest]:
    """The person's pending, unexpired requests, newest first."""
    _start_engine()
    current = now or utc_now()
    rows = AccountKeyRequest.filter(requester_uid=requester_uid, status="pending")
    pending = [row for row in rows if effective_status(row, now=current) == "pending"]
    return sorted(pending, key=lambda row: _aware(row.created_at), reverse=True)


def require_pending_account_key_request(
    request_uid: uuid.UUID | str,
    *,
    requester_uid: str,
    action: KeyRequestAction,
    environment: str | None = None,
    account_uid: str | None = None,
    now: dt.datetime | None = None,
) -> AccountKeyRequest:
    """Check that a submission may complete the request, before anything is written."""
    request = get_own_account_key_request(request_uid, requester_uid=requester_uid)
    status = effective_status(request, now=now)
    if status != "pending":
        raise ValueError(f"Account key request {request_uid!s} is {status}.")
    if request.action != action:
        raise ValueError(f"Account key request {request_uid!s} is for {request.action}.")
    if environment is not None and request.environment != environment:
        raise ValueError(
            f"Account key request {request_uid!s} is for a {request.environment} account."
        )
    if action == "rotate" and request.account_uid != account_uid:
        raise ValueError(f"Account key request {request_uid!s} is for another account.")
    return request


def complete_account_key_request(
    request_uid: uuid.UUID | str,
    *,
    account_uid: str,
    now: dt.datetime | None = None,
) -> AccountKeyRequest:
    return AccountKeyRequest.update(
        request_uid,
        {"status": "completed", "account_uid": account_uid, "completed_at": now or utc_now()},
    )


def cancel_account_key_request(
    request_uid: uuid.UUID | str,
    *,
    requester_uid: str,
    now: dt.datetime | None = None,
) -> AccountKeyRequest:
    """Cancel the person's own pending request; a finished request is left as it is."""
    request = get_own_account_key_request(request_uid, requester_uid=requester_uid)
    status = effective_status(request, now=now)
    if status != "pending":
        raise ValueError(f"Account key request {request_uid!s} is {status}.")
    return AccountKeyRequest.update(
        request_uid,
        {"status": "cancelled", "completed_at": now or utc_now()},
    )


__all__ = [
    "KEY_REQUEST_TTL",
    "AccountKeyRequest",
    "AccountKeyRequestTable",
    "KeyRequestAction",
    "KeyRequestStatus",
    "cancel_account_key_request",
    "complete_account_key_request",
    "create_account_key_request",
    "describe_account_key_request",
    "effective_status",
    "get_account_key_request",
    "get_own_account_key_request",
    "list_pending_account_key_requests",
    "require_pending_account_key_request",
]
