"""Durable step status for asset-registration operations.

Each row is one submitted plan or execution request. The ordered ``steps`` document is updated at
the same boundaries used by the API orchestration service, allowing browser clients to poll a stable
operation UID without coupling the storage layer to FastAPI.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar, Literal

from msm.api.base import MarketsMetaTableRow, operation_result_rows
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from pydantic import ConfigDict
from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin

OperationAction = Literal["plan", "execute"]
OperationStatus = Literal["queued", "running", "succeeded", "failed"]
StepStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]

UTC = dt.timezone.utc

_COMMON_STEPS: tuple[tuple[str, str], ...] = (
    ("prepare_scope", "Prepare registration scope"),
    ("load_alpaca_assets", "Load Alpaca credentials and asset catalog"),
    ("resolve_openfigi_identities", "Resolve OpenFIGI identities"),
    ("check_existing_assets", "Check existing Main Sequence assets"),
)
_FINAL_STEPS: dict[OperationAction, tuple[tuple[str, str], ...]] = {
    "plan": (("finalize_plan", "Finalize registration plan"),),
    "execute": (
        ("register_assets", "Register missing assets"),
        ("finalize_result", "Finalize registration result"),
    ),
}


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def registration_steps(action: OperationAction) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": label,
            "status": "pending",
            "message": None,
            "started_at": None,
            "completed_at": None,
        }
        for key, label in (*_COMMON_STEPS, *_FINAL_STEPS[action])
    ]


class AssetRegistrationOperationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """One durable status record for one asset-registration plan or execution request."""

    __project_storage_concept__ = "asset_registration_operation"
    __markets_base_identifier__ = "AssetRegistrationOperation"
    __metatable_description__ = (
        "Asset-registration operation records at one row per submitted plan or execution request. "
        "Rows retain ordered step progress, a sanitized terminal error or result, and timestamps so "
        "API clients can poll user-visible progress across requests."
    )
    __table_args__ = markets_table_args(
        "AssetRegistrationOperation",
        Index(None, "owner_uid"),
        Index(None, "status"),
        Index(None, "created_at"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={
            "label": "Operation UID",
            "description": "Stable public UUID used to poll this registration operation.",
        },
    )
    owner_uid: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        info={
            "label": "Owner UID",
            "description": (
                "Request-bound human user UID that owns this operation; null only for local "
                "development calls without injected identity context."
            ),
        },
    )
    action: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Action",
            "description": "Registration action requested by the caller: plan or execute.",
        },
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Status",
            "description": "Current operation state: queued, running, succeeded, or failed.",
        },
    )
    current_step: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        info={
            "label": "Current Step",
            "description": "Stable key of the running or most recently failed step.",
        },
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        info={
            "label": "Steps",
            "description": "Ordered step states, messages, and timestamps shown to the caller.",
        },
    )
    request: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        info={
            "label": "Request",
            "description": (
                "Validated non-secret registration inputs used to execute and explain this operation."
            ),
        },
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        info={
            "label": "Result",
            "description": "Terminal plan or execution response when the operation succeeds.",
        },
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        info={
            "label": "Error",
            "description": "Sanitized public error code, message, and retryability after failure.",
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={
            "label": "Created At",
            "description": "UTC time at which the API accepted the operation.",
        },
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Started At",
            "description": "UTC time at which background execution began.",
        },
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={
            "label": "Updated At",
            "description": "UTC time of the latest operation or step transition.",
        },
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Completed At",
            "description": "UTC time at which the operation succeeded or failed.",
        },
    )


class AssetRegistrationOperation(MarketsMetaTableRow):
    """Typed row operations for ``AssetRegistrationOperationTable``."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AssetRegistrationOperationTable]] = AssetRegistrationOperationTable
    __required_tables__: ClassVar[list[type[AssetRegistrationOperationTable]]] = [
        AssetRegistrationOperationTable
    ]

    owner_uid: str | None
    action: OperationAction
    status: OperationStatus
    current_step: str | None
    steps: list[dict[str, Any]]
    request: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    updated_at: dt.datetime
    completed_at: dt.datetime | None


def create_asset_registration_operation(
    *,
    action: OperationAction,
    request: dict[str, Any],
    owner_uid: str | None,
) -> AssetRegistrationOperation:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    from src.runtime import start_markets_engine

    now = utc_now()
    values = {
        "uid": new_markets_uid(),
        "owner_uid": owner_uid,
        "action": action,
        "status": "queued",
        "current_step": None,
        "steps": registration_steps(action),
        "request": request,
        "result": None,
        "error": None,
        "created_at": now,
        "started_at": None,
        "updated_at": now,
        "completed_at": None,
    }
    start_markets_engine()
    runtime = resolve_runtime(
        models=[AssetRegistrationOperationTable],
        row_model_name="AssetRegistrationOperation",
    )
    result = create_model(runtime.context, model=AssetRegistrationOperationTable, values=values)
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Asset registration operation create returned no row.")
    return AssetRegistrationOperation.model_validate(rows[0])


def get_asset_registration_operation(
    operation_uid: uuid.UUID | str,
) -> AssetRegistrationOperation | None:
    from src.runtime import start_markets_engine

    start_markets_engine()
    return AssetRegistrationOperation.get_by_uid(operation_uid)


def _updated_steps(
    operation: AssetRegistrationOperation,
    *,
    step_key: str,
    status: StepStatus,
    message: str | None,
) -> list[dict[str, Any]]:
    now = utc_now().isoformat()
    found = False
    updated: list[dict[str, Any]] = []
    for step in operation.steps:
        item = dict(step)
        if item["key"] == step_key:
            found = True
            item["status"] = status
            item["message"] = message
            if status == "running" and item.get("started_at") is None:
                item["started_at"] = now
            if status in {"succeeded", "failed", "skipped"}:
                item["completed_at"] = now
        updated.append(item)
    if not found:
        raise ValueError(f"Unknown registration operation step {step_key!r}.")
    return updated


def set_asset_registration_step(
    operation_uid: uuid.UUID | str,
    *,
    step_key: str,
    status: StepStatus,
    message: str | None = None,
) -> AssetRegistrationOperation:
    operation = get_asset_registration_operation(operation_uid)
    if operation is None:
        raise LookupError(f"Asset registration operation {operation_uid!s} does not exist.")
    now = utc_now()
    return AssetRegistrationOperation.update(
        operation_uid,
        {
            "status": "running" if status in {"running", "succeeded"} else operation.status,
            "current_step": step_key if status == "running" else None,
            "steps": _updated_steps(
                operation,
                step_key=step_key,
                status=status,
                message=message,
            ),
            "started_at": operation.started_at or now,
            "updated_at": now,
        },
    )


def complete_asset_registration_operation(
    operation_uid: uuid.UUID | str,
    *,
    result: dict[str, Any],
) -> AssetRegistrationOperation:
    operation = get_asset_registration_operation(operation_uid)
    if operation is None:
        raise LookupError(f"Asset registration operation {operation_uid!s} does not exist.")
    now = utc_now()
    return AssetRegistrationOperation.update(
        operation_uid,
        {
            "status": "succeeded",
            "current_step": None,
            "result": result,
            "error": None,
            "updated_at": now,
            "completed_at": now,
        },
    )


def fail_asset_registration_operation(
    operation_uid: uuid.UUID | str,
    *,
    step_key: str,
    error: dict[str, Any],
) -> AssetRegistrationOperation:
    operation = get_asset_registration_operation(operation_uid)
    if operation is None:
        raise LookupError(f"Asset registration operation {operation_uid!s} does not exist.")
    now = utc_now()
    failed_steps = _updated_steps(
        operation,
        step_key=step_key,
        status="failed",
        message=str(error["message"]),
    )
    for step in failed_steps:
        if step["status"] == "pending":
            step["status"] = "skipped"
            step["message"] = "Not run because a previous step failed."
            step["completed_at"] = now.isoformat()
    return AssetRegistrationOperation.update(
        operation_uid,
        {
            "status": "failed",
            "current_step": step_key,
            "steps": failed_steps,
            "error": error,
            "updated_at": now,
            "completed_at": now,
        },
    )


def project_operation_models() -> list[type[AssetRegistrationOperationTable]]:
    return [AssetRegistrationOperationTable]


__all__ = [
    "AssetRegistrationOperation",
    "AssetRegistrationOperationTable",
    "OperationAction",
    "OperationStatus",
    "StepStatus",
    "complete_asset_registration_operation",
    "create_asset_registration_operation",
    "fail_asset_registration_operation",
    "get_asset_registration_operation",
    "project_operation_models",
    "registration_steps",
    "set_asset_registration_step",
]
