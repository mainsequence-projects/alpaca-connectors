"""Durable desired state for Universe-backed Alpaca ETF signal Jobs.

One row is one signal update configuration and projects to exactly one Main Sequence Job.
The Job may create many JobRuns, but JobRuns never carry the business configuration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

from msm.api.base import MarketsMetaTableRow, operation_result_rows
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from msm.models import AccountTable
from pydantic import ConfigDict
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin
from src.universes.registry import AssetUniverseTable

ScheduleType = Literal["interval", "crontab"]
SchedulePeriod = Literal["seconds", "minutes", "hours", "days"]
LifecycleState = Literal["provisioning", "ready", "paused", "error", "deleting"]

SCHEDULE_TYPES: tuple[ScheduleType, ...] = ("interval", "crontab")
SCHEDULE_PERIODS: tuple[SchedulePeriod, ...] = ("seconds", "minutes", "hours", "days")
LIFECYCLE_STATES: tuple[LifecycleState, ...] = (
    "provisioning",
    "ready",
    "paused",
    "error",
    "deleting",
)
UTC = dt.timezone.utc
_UNSET = object()
_CRONTAB_FIELD_LIMITS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
)


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class AlpacaETFSignalJobConfigurationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """Desired business and operational state for one signal Job."""

    __project_storage_concept__ = "etf_signal_job_configuration"
    __markets_base_identifier__ = "AlpacaETFSignalJobConfiguration"
    __metatable_description__ = (
        "Durable configurations for Universe-backed Alpaca ETF signal Jobs. Each row links one "
        "Universe and one registered Alpaca account to exactly one Main Sequence Job; JobRuns "
        "resolve this row through their owning Job UID."
    )
    __table_args__ = markets_table_args(
        "AlpacaETFSignalJobConfiguration",
        CheckConstraint(
            "schedule_type IN ('interval', 'crontab')",
            name="ck_alpaca_etf_signal_job_schedule_type",
        ),
        CheckConstraint(
            "lifecycle_state IN ('provisioning', 'ready', 'paused', 'error', 'deleting')",
            name="ck_alpaca_etf_signal_job_lifecycle_state",
        ),
        CheckConstraint(
            "(schedule_type = 'interval' AND schedule_every IS NOT NULL "
            "AND schedule_every > 0 AND schedule_period IS NOT NULL "
            "AND schedule_expression IS NULL) OR "
            "(schedule_type = 'crontab' AND schedule_every IS NULL "
            "AND schedule_period IS NULL AND schedule_expression IS NOT NULL)",
            name="ck_alpaca_etf_signal_job_schedule_shape",
        ),
        CheckConstraint(
            "schedule_period IS NULL OR schedule_period IN ('seconds', 'minutes', 'hours', 'days')",
            name="ck_alpaca_etf_signal_job_schedule_period",
        ),
        CheckConstraint(
            "max_runtime_seconds > 0",
            name="ck_alpaca_etf_signal_job_max_runtime",
        ),
        Index(None, "name"),
        Index(None, "universe_uid", unique=True),
        Index(None, "account_uid"),
        Index(None, "job_uid", unique=True),
        Index(None, "enabled"),
        Index(None, "lifecycle_state"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={"label": "Configuration UID", "description": "Stable configuration identity."},
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={"label": "Name", "description": "User-facing signal Job name."},
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        info={"label": "Description", "description": "Optional operator-facing description."},
    )
    universe_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AssetUniverseTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Universe UID",
            "description": "Business identity and extraction source of the signal.",
        },
    )
    account_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AccountTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Account UID",
            "description": (
                "Runtime-only Alpaca credential source. It is excluded from signal identity and "
                "SignalWeightsStorage."
            ),
        },
    )
    job_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        info={
            "label": "Job UID",
            "description": "Main Sequence Job projected from this desired configuration.",
        },
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        info={
            "label": "Enabled",
            "description": "Whether the Job schedule and manual Run action are enabled.",
        },
    )
    schedule_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={"label": "Schedule Type", "description": "interval or crontab."},
    )
    schedule_every: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        info={"label": "Every", "description": "Positive interval quantity."},
    )
    schedule_period: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        info={"label": "Period", "description": "Interval unit."},
    )
    schedule_expression: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        info={"label": "Cron Expression", "description": "Five-field crontab expression."},
    )
    schedule_start_time: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={"label": "Starts At", "description": "Optional UTC schedule activation time."},
    )
    cpu_request: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={"label": "CPU", "description": "Requested vCPU units."},
    )
    memory_request: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={"label": "Memory", "description": "Requested GiB units."},
    )
    max_runtime_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        info={"label": "Maximum Runtime", "description": "Run timeout in seconds."},
    )
    spot: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        info={"label": "Spot", "description": "Whether the Job prefers spot capacity."},
    )
    lifecycle_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Lifecycle State",
            "description": "Projection state: provisioning, ready, paused, error, or deleting.",
        },
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        info={
            "label": "Last Error",
            "description": "Sanitized reconciliation failure; never provider credentials.",
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class AlpacaETFSignalJobConfiguration(MarketsMetaTableRow):
    """Typed row for one signal Job configuration."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AlpacaETFSignalJobConfigurationTable]] = (
        AlpacaETFSignalJobConfigurationTable
    )
    __required_tables__: ClassVar[list[type[MarketsBase]]] = [
        AssetUniverseTable,
        AccountTable,
        AlpacaETFSignalJobConfigurationTable,
    ]

    name: str
    description: str | None
    universe_uid: uuid.UUID
    account_uid: uuid.UUID
    job_uid: uuid.UUID | None
    enabled: bool
    schedule_type: ScheduleType
    schedule_every: int | None
    schedule_period: SchedulePeriod | None
    schedule_expression: str | None
    schedule_start_time: dt.datetime | None
    cpu_request: str
    memory_request: str
    max_runtime_seconds: int
    spot: bool
    lifecycle_state: LifecycleState
    last_error: str | None
    created_at: dt.datetime
    updated_at: dt.datetime


def _normalize_name(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Signal Job configuration name must not be empty.")
    if len(normalized) > 255:
        raise ValueError("Signal Job configuration name must be at most 255 characters.")
    return normalized


def _normalize_description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _validate_crontab_field(
    value: str,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> None:
    """Validate the numeric five-field syntax supported by the Signal schedule UI."""
    for part in value.split(","):
        range_part, separator, step_part = part.partition("/")
        if separator:
            if not step_part.isdigit() or int(step_part) < 1 or "/" in step_part:
                raise ValueError(f"Invalid crontab {label} step {step_part!r}.")
        if range_part == "*":
            continue
        bounds = range_part.split("-")
        if len(bounds) > 2 or any(not bound.isdigit() for bound in bounds):
            raise ValueError(f"Invalid crontab {label} field {value!r}.")
        numbers = [int(bound) for bound in bounds]
        if any(number < minimum or number > maximum for number in numbers):
            raise ValueError(f"Crontab {label} must stay between {minimum} and {maximum}.")
        if len(numbers) == 2 and numbers[0] > numbers[1]:
            raise ValueError(f"Invalid descending crontab {label} range {range_part!r}.")


def _normalize_crontab_expression(value: str | None) -> str:
    expression = " ".join(str(value or "").strip().split())
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("schedule_expression must contain exactly five crontab fields.")
    for field, (label, minimum, maximum) in zip(
        fields,
        _CRONTAB_FIELD_LIMITS,
        strict=True,
    ):
        _validate_crontab_field(
            field,
            label=label,
            minimum=minimum,
            maximum=maximum,
        )
    return expression


def normalize_schedule(
    *,
    schedule_type: str,
    schedule_every: int | None = None,
    schedule_period: str | None = None,
    schedule_expression: str | None = None,
    schedule_start_time: dt.datetime | None = None,
) -> dict[str, Any]:
    """Validate the durable schedule shape using the same contract as Main Sequence Jobs."""
    from mainsequence.client.models_helpers import CrontabSchedule, IntervalSchedule

    normalized_type = str(schedule_type or "").strip().lower()
    start_time = schedule_start_time
    if start_time is not None:
        if start_time.tzinfo is None:
            raise ValueError("schedule_start_time must include a timezone.")
        start_time = start_time.astimezone(UTC)
    if normalized_type == "interval":
        schedule = IntervalSchedule(
            every=schedule_every,
            period=str(schedule_period or "").strip().lower(),
            start_time=start_time,
        )
        return {
            "schedule_type": schedule.type,
            "schedule_every": schedule.every,
            "schedule_period": schedule.period,
            "schedule_expression": None,
            "schedule_start_time": schedule.start_time,
        }
    if normalized_type == "crontab":
        expression = _normalize_crontab_expression(schedule_expression)
        schedule = CrontabSchedule(expression=expression, start_time=start_time)
        return {
            "schedule_type": schedule.type,
            "schedule_every": None,
            "schedule_period": None,
            "schedule_expression": schedule.expression,
            "schedule_start_time": schedule.start_time,
        }
    raise ValueError(f"schedule_type must be one of {list(SCHEDULE_TYPES)!r}.")


def validate_signal_job_references(
    *,
    universe_uid: uuid.UUID,
    account_uid: uuid.UUID,
) -> None:
    from src.account.services import get_account_registration
    from src.universes import get_asset_universe

    universe = get_asset_universe(universe_uid)
    if universe is None:
        raise LookupError(f"Asset Universe {universe_uid!s} does not exist.")
    if not universe.is_active:
        raise ValueError(f"Asset Universe {universe_uid!s} is inactive.")
    account = get_account_registration(account_uid)
    if account is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    if not bool(account["account_is_active"]):
        raise ValueError(f"Alpaca account registration {account_uid!s} is inactive.")


def create_signal_job_configuration_row(
    *,
    name: str,
    universe_uid: uuid.UUID | str,
    account_uid: uuid.UUID | str,
    schedule_type: str,
    schedule_every: int | None = None,
    schedule_period: str | None = None,
    schedule_expression: str | None = None,
    schedule_start_time: dt.datetime | None = None,
    description: str | None = None,
    enabled: bool = True,
    cpu_request: str = "0.25",
    memory_request: str = "0.5",
    max_runtime_seconds: int = 3600,
    spot: bool = False,
    uid: uuid.UUID | str | None = None,
) -> AlpacaETFSignalJobConfiguration:
    """Create desired state before provisioning its platform Job."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    from src.runtime import account_runtime_models, start_markets_engine

    normalized_universe_uid = uuid.UUID(str(universe_uid))
    normalized_account_uid = uuid.UUID(str(account_uid))
    schedule = normalize_schedule(
        schedule_type=schedule_type,
        schedule_every=schedule_every,
        schedule_period=schedule_period,
        schedule_expression=schedule_expression,
        schedule_start_time=schedule_start_time,
    )
    if max_runtime_seconds <= 0:
        raise ValueError("max_runtime_seconds must be positive.")
    start_markets_engine(models=account_runtime_models())
    validate_signal_job_references(
        universe_uid=normalized_universe_uid,
        account_uid=normalized_account_uid,
    )
    if signal_job_configurations_for_universe(normalized_universe_uid):
        raise ValueError(
            f"Asset Universe {normalized_universe_uid!s} already has a signal Job configuration "
            "in this Environment."
        )
    now = utc_now()
    values = {
        "uid": uuid.UUID(str(uid)) if uid is not None else new_markets_uid(),
        "name": _normalize_name(name),
        "description": _normalize_description(description),
        "universe_uid": normalized_universe_uid,
        "account_uid": normalized_account_uid,
        "job_uid": None,
        "enabled": bool(enabled),
        **schedule,
        "cpu_request": str(cpu_request).strip(),
        "memory_request": str(memory_request).strip(),
        "max_runtime_seconds": int(max_runtime_seconds),
        "spot": bool(spot),
        "lifecycle_state": "provisioning",
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    runtime = resolve_runtime(
        models=[AlpacaETFSignalJobConfigurationTable],
        row_model_name="AlpacaETFSignalJobConfiguration",
    )
    result = create_model(
        runtime.context,
        model=AlpacaETFSignalJobConfigurationTable,
        values=values,
    )
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Signal Job configuration create returned no row.")
    return AlpacaETFSignalJobConfiguration.model_validate(rows[0])


def get_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaETFSignalJobConfiguration | None:
    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    return AlpacaETFSignalJobConfiguration.get_by_uid(configuration_uid)


def signal_job_configurations_by_uids(
    configuration_uids: Sequence[uuid.UUID | str],
) -> dict[str, AlpacaETFSignalJobConfiguration]:
    """Load a Signal Job configuration UID set with one governed backend query."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from src.runtime import account_runtime_models, start_markets_engine

    normalized_uids = list(
        dict.fromkeys(uuid.UUID(str(configuration_uid)) for configuration_uid in configuration_uids)
    )
    if not normalized_uids:
        return {}
    start_markets_engine(models=account_runtime_models())
    runtime = resolve_runtime(
        models=[AlpacaETFSignalJobConfigurationTable],
        row_model_name="AlpacaETFSignalJobConfiguration",
    )
    operation = compile_markets_statement(
        select(AlpacaETFSignalJobConfigurationTable).where(
            AlpacaETFSignalJobConfigurationTable.uid.in_(normalized_uids)
        ),
        context=runtime.context,
        operation="select",
        models=[AlpacaETFSignalJobConfigurationTable],
        access="read",
    )
    rows = [
        AlpacaETFSignalJobConfiguration.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]
    return {str(row.uid): row for row in rows}


def get_signal_job_configuration_by_job_uid(
    job_uid: uuid.UUID | str,
) -> AlpacaETFSignalJobConfiguration | None:
    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    rows = AlpacaETFSignalJobConfiguration.filter(job_uid=str(job_uid), limit=2)
    if len(rows) > 1:
        raise RuntimeError(f"Job {job_uid!s} is linked to multiple signal configurations.")
    return rows[0] if rows else None


def signal_job_configurations_for_universe(
    universe_uid: uuid.UUID | str,
) -> list[AlpacaETFSignalJobConfiguration]:
    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    return AlpacaETFSignalJobConfiguration.filter(universe_uid=str(universe_uid), limit=2)


def signal_job_configurations_for_account(
    account_uid: uuid.UUID | str,
) -> list[AlpacaETFSignalJobConfiguration]:
    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    return AlpacaETFSignalJobConfiguration.filter(account_uid=str(account_uid), limit=10_000)


def list_signal_job_configurations(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    enabled: bool | None = None,
    lifecycle_state: str | None = None,
    ordering: str = "name",
) -> tuple[list[AlpacaETFSignalJobConfiguration], int]:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    runtime = resolve_runtime(
        models=[AlpacaETFSignalJobConfigurationTable],
        row_model_name="AlpacaETFSignalJobConfiguration",
    )
    statement = select(AlpacaETFSignalJobConfigurationTable)
    if enabled is not None:
        statement = statement.where(AlpacaETFSignalJobConfigurationTable.enabled == enabled)
    if lifecycle_state is not None:
        normalized_state = lifecycle_state.strip().lower()
        if normalized_state not in LIFECYCLE_STATES:
            raise ValueError(f"Unsupported lifecycle_state filter {lifecycle_state!r}.")
        statement = statement.where(
            AlpacaETFSignalJobConfigurationTable.lifecycle_state == normalized_state
        )
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                AlpacaETFSignalJobConfigurationTable.name.ilike(pattern),
                AlpacaETFSignalJobConfigurationTable.description.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(
        statement.order_by(None).subquery()
    )
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "name": AlpacaETFSignalJobConfigurationTable.name,
        "updated_at": AlpacaETFSignalJobConfigurationTable.updated_at,
        "lifecycle_state": AlpacaETFSignalJobConfigurationTable.lifecycle_state,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported signal Job configuration ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    statement = statement.order_by(
        ordering_expression,
        AlpacaETFSignalJobConfigurationTable.uid.asc(),
    )
    page_operation = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
        models=[AlpacaETFSignalJobConfigurationTable],
        access="read",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[AlpacaETFSignalJobConfigurationTable],
        access="read",
    )
    rows = [
        AlpacaETFSignalJobConfiguration.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(page_operation, context=runtime.context)
        )
    ]
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    return rows, int(count_rows[0].get("count", 0)) if count_rows else 0


def update_signal_job_configuration_row(
    configuration_uid: uuid.UUID | str,
    *,
    values: dict[str, Any],
) -> AlpacaETFSignalJobConfiguration:
    """Apply already-normalized desired or reconciliation state."""
    if get_signal_job_configuration(configuration_uid) is None:
        raise LookupError(f"Signal Job configuration {configuration_uid!s} does not exist.")
    return AlpacaETFSignalJobConfiguration.update(
        configuration_uid,
        {**values, "updated_at": utc_now()},
    )


def delete_signal_job_configuration_row(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    if get_signal_job_configuration(configuration_uid) is None:
        raise LookupError(f"Signal Job configuration {configuration_uid!s} does not exist.")
    return AlpacaETFSignalJobConfiguration.delete(configuration_uid)


def project_signal_job_models() -> list[type[MarketsBase]]:
    return [AlpacaETFSignalJobConfigurationTable]


__all__ = [
    "LIFECYCLE_STATES",
    "SCHEDULE_PERIODS",
    "SCHEDULE_TYPES",
    "AlpacaETFSignalJobConfiguration",
    "AlpacaETFSignalJobConfigurationTable",
    "LifecycleState",
    "SchedulePeriod",
    "ScheduleType",
    "create_signal_job_configuration_row",
    "delete_signal_job_configuration_row",
    "get_signal_job_configuration",
    "get_signal_job_configuration_by_job_uid",
    "list_signal_job_configurations",
    "normalize_schedule",
    "project_signal_job_models",
    "signal_job_configurations_for_account",
    "signal_job_configurations_for_universe",
    "signal_job_configurations_by_uids",
    "update_signal_job_configuration_row",
    "validate_signal_job_references",
]
