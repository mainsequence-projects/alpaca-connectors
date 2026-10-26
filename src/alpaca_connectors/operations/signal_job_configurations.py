"""Durable Universe-backed Alpaca ETF signal configurations.

One row is one signal update configuration: its Universe, its runtime Alpaca account, and one
operational flag. The flag is stored in the ``enabled`` column and exposed as ``scheduled``: it
decides whether the scheduled run of the signal Job declared in
``.mainsequence/workflows/alpaca-connectors-api.yaml`` includes the configuration (ADR 0013). The
Job owns schedule, compute and image; JobRuns own execution state and never carry the business
configuration.

Revision 0009 created this table for the former one-Job-per-configuration design (ADR 0007), so it
still has NOT NULL schedule, compute and lifecycle columns plus the ``job_uid`` link. Nothing reads
them any more. Until a later revision drops them, a new row fills them with placeholders that keep
revision 0009's constraints and the previous release's row model satisfied.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, ClassVar

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

from alpaca_connectors.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin
from alpaca_connectors.universes.registry import AssetUniverseTable

UTC = dt.timezone.utc

# Insert-only values for the NOT NULL columns of the former one-Job-per-configuration design
# (ADR 0007): the former API defaults (a daily interval, 0.25 CPU, 0.5 GiB, 3600 seconds, no spot)
# and the state revision-0009 code gave a row before it linked a Job. `job_uid` and the other
# nullable retired columns stay NULL. Nothing reads any of them.
RETIRED_JOB_COLUMN_VALUES: dict[str, Any] = {
    "schedule_type": "interval",
    "schedule_every": 1,
    "schedule_period": "days",
    "cpu_request": "0.25",
    "memory_request": "0.5",
    "max_runtime_seconds": 3600,
    "spot": False,
    "lifecycle_state": "provisioning",
}


def _former_job_column(subject: str, values: str) -> str:
    """Column description of one retired Job column; ``values`` says what rows hold."""
    return (
        f"No longer used: {subject} of the dedicated Job an earlier design ran per "
        f"configuration; {values}."
    )


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class AlpacaETFSignalJobConfigurationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """Business configuration and scheduled-run flag of one ETF weight signal."""

    __project_storage_concept__ = "etf_signal_job_configuration"
    __markets_base_identifier__ = "AlpacaETFSignalJobConfiguration"
    __metatable_description__ = (
        "One row per ETF weight signal. Saved settings for a signal that publishes an ETF's "
        "holdings weights as portfolio target weights: the asset universe the fund's holdings "
        "come from, the registered Alpaca brokerage account whose keys a run uses, and whether "
        "the scheduled signal update includes it; created by operators of the Alpaca Connectors "
        "application."
    )
    __metatable_labels__ = ["etfs", "signals", "configuration", "alpaca"]
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
            "AND schedule_expression IS NULL AND schedule_timezone IS NULL) OR "
            "(schedule_type = 'crontab' AND schedule_every IS NULL "
            "AND schedule_period IS NULL AND schedule_expression IS NOT NULL "
            "AND schedule_timezone IS NOT NULL)",
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
        info={
            "label": "Configuration UID",
            "description": "Public UUID that identifies the signal and selects it to run.",
        },
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={"label": "Name", "description": "Display name of the signal."},
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        info={
            "label": "Description",
            "description": "Free-text note about the signal; null when none was given.",
        },
    )
    universe_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AssetUniverseTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Universe UID",
            "description": (
                "UID of the asset universe whose ETF holdings the signal publishes as weights; it "
                "defines the signal's identity, and each universe has at most one signal."
            ),
        },
    )
    account_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AccountTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Account UID",
            "description": (
                "Main Sequence account UID of the registered Alpaca brokerage account whose API "
                "keys a run uses; it is not part of the signal's identity or published weights."
            ),
        },
    )
    job_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        info={
            "label": "Job UID",
            "description": _former_job_column("UID", "null on rows created since"),
        },
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        info={
            "label": "Scheduled",
            "description": "Whether the scheduled signal update includes this configuration.",
        },
    )
    schedule_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Schedule type",
            "description": _former_job_column(
                "schedule kind, interval or crontab,", "new rows hold interval"
            ),
        },
    )
    schedule_every: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        info={
            "label": "Schedule interval",
            "description": _former_job_column(
                "number of periods between runs", "null for a crontab schedule, and 1 on new rows"
            ),
        },
    )
    schedule_period: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        info={
            "label": "Schedule unit",
            "description": _former_job_column(
                "interval unit, seconds, minutes, hours or days,",
                "null for a crontab schedule, and days on new rows",
            ),
        },
    )
    schedule_expression: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        info={
            "label": "Cron expression",
            "description": _former_job_column(
                "cron expression", "null for an interval schedule, as on every new row"
            ),
        },
    )
    schedule_timezone: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        info={
            "label": "Schedule timezone",
            "description": _former_job_column(
                "IANA timezone of the cron schedule",
                "null for an interval schedule, as on every new row",
            ),
        },
    )
    schedule_start_time: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Schedule start",
            "description": _former_job_column(
                "first run time, in UTC,", "null when none was set, as on every new row"
            ),
        },
    )
    cpu_request: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "CPU request",
            "description": _former_job_column("CPU request, in cores,", "new rows hold 0.25"),
        },
    )
    memory_request: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Memory request",
            "description": _former_job_column("memory request, in GiB,", "new rows hold 0.5"),
        },
    )
    max_runtime_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        info={
            "label": "Maximum runtime",
            "description": _former_job_column("run time limit, in seconds,", "new rows hold 3600"),
        },
    )
    spot: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        info={
            "label": "Spot capacity",
            "description": _former_job_column(
                "flag for running on spot capacity", "new rows hold false"
            ),
        },
    )
    lifecycle_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Lifecycle state",
            "description": _former_job_column(
                "provisioning state (provisioning, ready, paused, error or deleting)",
                "new rows hold provisioning",
            ),
        },
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        info={
            "label": "Last error",
            "description": _former_job_column(
                "last provisioning error", "null when there was none, as on every new row"
            ),
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Created at", "description": "Time, in UTC, the signal was configured."},
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        info={
            "label": "Updated at",
            "description": "Time, in UTC, the configuration last changed.",
        },
    )


class AlpacaETFSignalJobConfiguration(MarketsMetaTableRow):
    """Typed row for one signal configuration; the retired Job columns are not read."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AlpacaETFSignalJobConfigurationTable]] = (
        AlpacaETFSignalJobConfigurationTable
    )
    # Own tables only; the Account foreign key is a reference (ms-markets ADR 0047).
    __required_tables__: ClassVar[list[type[MarketsBase]]] = [
        AssetUniverseTable,
        AlpacaETFSignalJobConfigurationTable,
    ]

    name: str
    description: str | None
    universe_uid: uuid.UUID
    account_uid: uuid.UUID
    enabled: bool
    created_at: dt.datetime
    updated_at: dt.datetime

    @property
    def scheduled(self) -> bool:
        """Whether the shared signal Job's scheduled run includes this configuration."""
        return self.enabled


def _normalize_name(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Signal configuration name must not be empty.")
    if len(normalized) > 255:
        raise ValueError("Signal configuration name must be at most 255 characters.")
    return normalized


def _normalize_description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def validate_signal_job_references(
    *,
    universe_uid: uuid.UUID,
    account_uid: uuid.UUID,
) -> None:
    from alpaca_connectors.account.services import get_account_registration
    from alpaca_connectors.universes import get_asset_universe

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
    description: str | None = None,
    scheduled: bool = True,
    uid: uuid.UUID | str | None = None,
) -> AlpacaETFSignalJobConfiguration:
    """Store one signal configuration; it touches no platform Job."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    normalized_universe_uid = uuid.UUID(str(universe_uid))
    normalized_account_uid = uuid.UUID(str(account_uid))
    start_markets_engine(models=account_runtime_models())
    validate_signal_job_references(
        universe_uid=normalized_universe_uid,
        account_uid=normalized_account_uid,
    )
    if signal_job_configurations_for_universe(normalized_universe_uid):
        raise ValueError(
            f"Asset Universe {normalized_universe_uid!s} already has a signal configuration "
            "in this Environment."
        )
    now = utc_now()
    values = {
        **RETIRED_JOB_COLUMN_VALUES,
        "uid": uuid.UUID(str(uid)) if uid is not None else new_markets_uid(),
        "name": _normalize_name(name),
        "description": _normalize_description(description),
        "universe_uid": normalized_universe_uid,
        "account_uid": normalized_account_uid,
        "enabled": bool(scheduled),
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
        raise RuntimeError("Signal configuration create returned no row.")
    return AlpacaETFSignalJobConfiguration.model_validate(rows[0])


def get_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaETFSignalJobConfiguration | None:
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    return AlpacaETFSignalJobConfiguration.get_by_uid(configuration_uid)


def _select_rows(statement: Any) -> list[AlpacaETFSignalJobConfiguration]:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation

    runtime = resolve_runtime(
        models=[AlpacaETFSignalJobConfigurationTable],
        row_model_name="AlpacaETFSignalJobConfiguration",
    )
    operation = compile_markets_statement(statement, context=runtime.context, operation="select")
    return [
        AlpacaETFSignalJobConfiguration.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]


def signal_job_configurations_by_uids(
    configuration_uids: Sequence[uuid.UUID | str],
) -> dict[str, AlpacaETFSignalJobConfiguration]:
    """Load a signal configuration UID set with one governed backend query."""
    from sqlalchemy import select

    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    normalized_uids = list(
        dict.fromkeys(uuid.UUID(str(configuration_uid)) for configuration_uid in configuration_uids)
    )
    if not normalized_uids:
        return {}
    start_markets_engine(models=account_runtime_models())
    rows = _select_rows(
        select(AlpacaETFSignalJobConfigurationTable).where(
            AlpacaETFSignalJobConfigurationTable.uid.in_(normalized_uids)
        )
    )
    return {str(row.uid): row for row in rows}


def scheduled_signal_configurations() -> list[AlpacaETFSignalJobConfiguration]:
    """Return every signal configuration the signal Job's scheduled run includes."""
    from sqlalchemy import select

    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    table = AlpacaETFSignalJobConfigurationTable
    start_markets_engine(models=account_runtime_models())
    return _select_rows(
        select(table).where(table.enabled.is_(True)).order_by(table.name.asc(), table.uid.asc())
    )


def signal_job_configurations_for_universe(
    universe_uid: uuid.UUID | str,
) -> list[AlpacaETFSignalJobConfiguration]:
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    return AlpacaETFSignalJobConfiguration.filter(universe_uid=str(universe_uid), limit=2)


def signal_job_configurations_for_account(
    account_uid: uuid.UUID | str,
) -> list[AlpacaETFSignalJobConfiguration]:
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    return AlpacaETFSignalJobConfiguration.filter(account_uid=str(account_uid), limit=10_000)


def list_signal_job_configurations(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    scheduled: bool | None = None,
    ordering: str = "name",
) -> tuple[list[AlpacaETFSignalJobConfiguration], int]:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    runtime = resolve_runtime(
        models=[AlpacaETFSignalJobConfigurationTable],
        row_model_name="AlpacaETFSignalJobConfiguration",
    )
    statement = select(AlpacaETFSignalJobConfigurationTable)
    if scheduled is not None:
        statement = statement.where(AlpacaETFSignalJobConfigurationTable.enabled == scheduled)
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
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported signal configuration ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    statement = statement.order_by(
        ordering_expression,
        AlpacaETFSignalJobConfigurationTable.uid.asc(),
    )
    rows = _select_rows(statement.limit(limit).offset(offset))
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
    )
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    return rows, int(count_rows[0].get("count", 0)) if count_rows else 0


def update_signal_job_configuration_row(
    configuration_uid: uuid.UUID | str,
    *,
    values: dict[str, Any],
) -> AlpacaETFSignalJobConfiguration:
    """Apply already-normalized configuration values."""
    if get_signal_job_configuration(configuration_uid) is None:
        raise LookupError(f"Signal configuration {configuration_uid!s} does not exist.")
    return AlpacaETFSignalJobConfiguration.update(
        configuration_uid,
        {**values, "updated_at": utc_now()},
    )


def delete_signal_job_configuration_row(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    if get_signal_job_configuration(configuration_uid) is None:
        raise LookupError(f"Signal configuration {configuration_uid!s} does not exist.")
    return AlpacaETFSignalJobConfiguration.delete(configuration_uid)


def project_signal_job_models() -> list[type[MarketsBase]]:
    return [AlpacaETFSignalJobConfigurationTable]


__all__ = [
    "RETIRED_JOB_COLUMN_VALUES",
    "AlpacaETFSignalJobConfiguration",
    "AlpacaETFSignalJobConfigurationTable",
    "create_signal_job_configuration_row",
    "delete_signal_job_configuration_row",
    "get_signal_job_configuration",
    "list_signal_job_configurations",
    "project_signal_job_models",
    "scheduled_signal_configurations",
    "signal_job_configurations_by_uids",
    "signal_job_configurations_for_account",
    "signal_job_configurations_for_universe",
    "update_signal_job_configuration_row",
    "validate_signal_job_references",
]
