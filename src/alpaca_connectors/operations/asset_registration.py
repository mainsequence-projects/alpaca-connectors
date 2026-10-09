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
from msm.api.http import OperationError, OperationStatus, OperationStep
from msm.api.http import OperationStepStatus as StepStatus
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from pydantic import ConfigDict
from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from alpaca_connectors.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin

OperationAction = Literal["plan", "execute"]

UTC = dt.timezone.utc

_COMMON_STEPS: tuple[tuple[str, str], ...] = (
    ("prepare_scope", "Prepare registration scope"),
    ("resolve_account", "Resolve registered Alpaca account"),
    ("load_alpaca_assets", "Load Alpaca asset catalog"),
    ("resolve_alpaca_identities", "Resolve Alpaca asset identities"),
    ("enrich_openfigi_details", "Enrich optional OpenFIGI details"),
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
        OperationStep(key=key, label=label).model_dump(mode="json")
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
        "One row per asset registration request. Progress and outcome of requests to plan or "
        "register US stocks and ETFs from Alpaca as Main Sequence assets: who asked, each step's "
        "state, the result or a sanitized error, and timestamps, recorded by the Alpaca "
        "Connectors API while the request runs so that clients can follow it."
    )
    __metatable_labels__ = ["equities", "reference-data", "operations", "alpaca"]
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
            "description": "Public UUID clients poll to follow the request.",
        },
    )
    owner_uid: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        info={
            "label": "Owner UID",
            "description": (
                "Main Sequence user UID of the person who submitted the request; null only for a "
                "local development call made without a signed-in user."
            ),
        },
    )
    action: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Action",
            "description": (
                "plan to preview the registration without writing, or execute to register the "
                "missing assets."
            ),
        },
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Status",
            "description": "Request state: queued, running, succeeded or failed.",
        },
    )
    current_step: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        info={
            "label": "Current step",
            "description": (
                "Key of the step now running or of the step that failed, such as "
                "load_alpaca_assets; null while queued, between steps and after success."
            ),
        },
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        info={
            "label": "Steps",
            "description": (
                "JSON list of the request's steps in order, each with its key, label, status "
                "(pending, running, succeeded, failed or skipped), message and UTC start and end "
                "times."
            ),
        },
    )
    request: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        info={
            "label": "Request",
            "description": (
                "JSON object with the validated registration inputs, such as the account UID and "
                "ticker symbols; it never holds credential values."
            ),
        },
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        info={
            "label": "Result",
            "description": (
                "JSON plan or registration result returned to the client; null until the request "
                "succeeds."
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
                "null unless the request failed."
            ),
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={
            "label": "Created at",
            "description": "Time, in UTC, the API accepted the request.",
        },
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Started at",
            "description": "Time, in UTC, the request started running; null while queued.",
        },
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={
            "label": "Updated at",
            "description": "Time, in UTC, the request or one of its steps last changed state.",
        },
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Completed at",
            "description": (
                "Time, in UTC, the request succeeded or failed; null while queued or running."
            ),
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
    steps: list[OperationStep]
    request: dict[str, Any]
    result: dict[str, Any] | None
    error: OperationError | None
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

    from alpaca_connectors.runtime import start_markets_engine

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
    from alpaca_connectors.runtime import start_markets_engine

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
        item = OperationStep.model_validate(step).model_dump(mode="json")
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
    validated_error = OperationError.model_validate(error)
    failed_steps = _updated_steps(
        operation,
        step_key=step_key,
        status="failed",
        message=validated_error.message,
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
            "error": validated_error.model_dump(mode="json"),
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
