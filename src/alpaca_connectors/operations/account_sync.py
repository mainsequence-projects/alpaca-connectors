"""Durable records of account syncs (issue #14).

`POST /v1/accounts/{account_uid}/actions/sync` reads the Alpaca account with its stored keys and
publishes one `AccountHoldingsSet` into ms-markets. Each call is one row here, in the shape of
`msm.api.http.ObservableOperation`, so `GET /v1/operations/{operation_uid}` can answer it again,
from any API process, to the owner it ran for. The owner is the acting owner of ADR 0019. A row
holds identifiers, counts and a public error, never a key value.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from msm.api.base import MarketsMetaTableRow
from msm.api.http import OperationError, OperationStatus, OperationStep
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from pydantic import ConfigDict
from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin

ACCOUNT_SYNC_ACTION = "account_sync"
CAPTURE_STEP = "capture_holdings"
ACCOUNT_SYNC_STEPS: tuple[tuple[str, str], ...] = (
    (CAPTURE_STEP, "Read the Alpaca account and publish its holdings"),
)

UTC = dt.timezone.utc


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class AccountSyncOperationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """One durable record of one account sync."""

    __project_storage_concept__ = "account_sync_operation"
    __markets_base_identifier__ = "AccountSyncOperation"
    __metatable_description__ = (
        "One row per account sync. An application asked the Alpaca Connectors API to read an "
        "Alpaca account with its stored keys and publish its holdings into Main Sequence "
        "Markets: whose sync it was, its steps, the published holdings snapshot or a sanitized "
        "error, and timestamps, so the application can read the outcome again."
    )
    __metatable_labels__ = ["equities", "holdings", "operations", "alpaca"]
    __table_args__ = markets_table_args(
        "AccountSyncOperation",
        Index(None, "owner_gateway_uid", "owner_user_uid"),
        Index(None, "account_uid"),
        Index(None, "created_at"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={
            "label": "Operation UID",
            "description": "Public UUID the application reads the sync back with.",
        },
    )
    owner_gateway_uid: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        info={
            "label": "Owner gateway UID",
            "description": (
                "Who the sync ran for, first half (ADR 0019): the Main Sequence user's own UID, "
                "or for an external person the UID of the gateway application that vouched for "
                "them."
            ),
        },
    )
    owner_user_uid: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={
            "label": "Owner user UID",
            "description": (
                "Who the sync ran for, second half (ADR 0019): the Main Sequence user's UID, or "
                "the external person's identifier at that gateway. Only this owner reads the "
                "sync back."
            ),
        },
    )
    account_uid: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={
            "label": "Account UID",
            "description": "UID of the Main Sequence Markets account that was synced.",
        },
    )
    action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={
            "label": "Action",
            "description": "Always account_sync.",
        },
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Status",
            "description": "Sync state: running, succeeded or failed.",
        },
    )
    current_step: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        info={
            "label": "Current step",
            "description": (
                "Key of the step now running or of the step that failed; null after success."
            ),
        },
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        info={
            "label": "Steps",
            "description": (
                "JSON list of the sync's steps in order, each with its key, label, status, "
                "message and UTC start and end times."
            ),
        },
    )
    request: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        info={
            "label": "Request",
            "description": "JSON object naming the synced account UID; never a key value.",
        },
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        info={
            "label": "Result",
            "description": (
                "JSON object with the account UID, the published holdings snapshot UID, its "
                "time, its row count and Alpaca-specific extras; null unless the sync succeeded."
            ),
        },
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        info={
            "label": "Error",
            "description": (
                "JSON object with the public error code, message and whether a retry may succeed; "
                "null unless the sync failed."
            ),
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Created at", "description": "Time, in UTC, the API accepted the sync."},
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={"label": "Started at", "description": "Time, in UTC, the sync started running."},
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={
            "label": "Updated at",
            "description": "Time, in UTC, the sync or one of its steps last changed state.",
        },
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Completed at",
            "description": "Time, in UTC, the sync succeeded or failed; null while running.",
        },
    )


class AccountSyncOperation(MarketsMetaTableRow):
    """Typed row operations for ``AccountSyncOperationTable``."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AccountSyncOperationTable]] = AccountSyncOperationTable
    __required_tables__: ClassVar[list[type[AccountSyncOperationTable]]] = [
        AccountSyncOperationTable
    ]

    owner_gateway_uid: str
    owner_user_uid: str
    account_uid: str
    action: str
    status: OperationStatus
    current_step: str | None
    steps: list[OperationStep]
    request: dict[str, Any]
    result: dict[str, Any] | None
    error: OperationError | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    updated_at: dt.datetime
    completed_at: dt.datetime | None

    @property
    def owner(self) -> AccountOwner:
        return AccountOwner(gateway_uid=self.owner_gateway_uid, user_uid=self.owner_user_uid)


def _start_engine() -> None:
    from alpaca_connectors.runtime import start_markets_engine

    start_markets_engine()


def start_account_sync_operation(
    *,
    account_uid: str,
    owner: AccountOwner,
) -> AccountSyncOperation:
    """Record a sync that starts now, with its one step running."""
    now = utc_now()
    steps = [
        OperationStep(
            key=key,
            label=label,
            status="running" if key == CAPTURE_STEP else "pending",
            started_at=now if key == CAPTURE_STEP else None,
        ).model_dump(mode="json")
        for key, label in ACCOUNT_SYNC_STEPS
    ]
    _start_engine()
    return AccountSyncOperation.create(
        {
            "uid": new_markets_uid(),
            **owner.column_values(),
            "account_uid": account_uid,
            "action": ACCOUNT_SYNC_ACTION,
            "status": "running",
            "current_step": CAPTURE_STEP,
            "steps": steps,
            "request": {"account_uid": account_uid},
            "result": None,
            "error": None,
            "created_at": now,
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
        }
    )


def _finished_steps(
    operation: AccountSyncOperation,
    *,
    status: str,
    message: str | None,
    now: dt.datetime,
) -> list[dict[str, Any]]:
    finished: list[dict[str, Any]] = []
    for step in operation.steps:
        item = step.model_dump(mode="json")
        if item["key"] == operation.current_step:
            item.update(status=status, message=message, completed_at=now.isoformat())
        elif item["status"] == "pending":
            item.update(
                status="skipped",
                message="Not run because a previous step failed." if status == "failed" else None,
                completed_at=now.isoformat(),
            )
        finished.append(item)
    return finished


def succeed_account_sync_operation(
    operation: AccountSyncOperation,
    *,
    result: dict[str, Any],
) -> AccountSyncOperation:
    now = utc_now()
    return AccountSyncOperation.update(
        operation.uid,
        {
            "status": "succeeded",
            "current_step": None,
            "steps": _finished_steps(operation, status="succeeded", message=None, now=now),
            "result": result,
            "error": None,
            "updated_at": now,
            "completed_at": now,
        },
    )


def fail_account_sync_operation(
    operation: AccountSyncOperation,
    *,
    error: OperationError,
) -> AccountSyncOperation:
    now = utc_now()
    return AccountSyncOperation.update(
        operation.uid,
        {
            "status": "failed",
            "current_step": operation.current_step,
            "steps": _finished_steps(operation, status="failed", message=error.message, now=now),
            "error": error.model_dump(mode="json"),
            "updated_at": now,
            "completed_at": now,
        },
    )


def get_account_sync_operation(operation_uid: uuid.UUID | str) -> AccountSyncOperation | None:
    _start_engine()
    return AccountSyncOperation.get_by_uid(operation_uid)


def project_operation_models() -> list[type[AccountSyncOperationTable]]:
    return [AccountSyncOperationTable]


__all__ = [
    "ACCOUNT_SYNC_ACTION",
    "ACCOUNT_SYNC_STEPS",
    "CAPTURE_STEP",
    "AccountSyncOperation",
    "AccountSyncOperationTable",
    "fail_account_sync_operation",
    "get_account_sync_operation",
    "project_operation_models",
    "start_account_sync_operation",
    "succeed_account_sync_operation",
]
