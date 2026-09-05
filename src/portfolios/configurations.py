"""Durable analytical portfolio and rebalance configurations.

The rows in this module own calculation intent only. Scheduling, compute, image selection,
automatic deployment, and execution history remain properties of the linked Main Sequence Job
and its JobRuns.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

from msm.api.base import MarketsMetaTableRow, operation_result_rows
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from msm.models.portfolios import PortfolioTable
from pydantic import ConfigDict
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.market_data.configurations import AlpacaBarsConfigurationTable
from src.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin

RebalanceStrategy = Literal["immediate_signal"]
REBALANCE_STRATEGIES: tuple[RebalanceStrategy, ...] = ("immediate_signal",)
SIGNAL_JOB_CONFIGURATION_TABLE_NAME = "alpaca_connectors__etf_signal_job_configuration"
UTC = dt.timezone.utc
_UNSET = object()


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class PortfolioRebalanceConfigurationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """Reusable configuration for converting signal weights into executed weights."""

    __project_storage_concept__ = "portfolio_rebalance_configuration"
    __markets_base_identifier__ = "PortfolioRebalanceConfiguration"
    __metatable_description__ = (
        "Reusable portfolio rebalance configurations. Phase 1 supports ImmediateSignal only."
    )
    __table_args__ = markets_table_args(
        "PortfolioRebalanceConfiguration",
        CheckConstraint(
            "strategy = 'immediate_signal'",
            name="ck_portfolio_rebalance_configuration_strategy",
        ),
        Index(None, "name"),
        Index(None, "strategy"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={"label": "Configuration UID", "description": "Stable configuration identity."},
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={
            "label": "Strategy",
            "description": "ImmediateSignal in the initial analytical backtest release.",
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class AlpacaETFPortfolioConfigurationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """One persistent analytical portfolio definition and its platform Job relationship."""

    __project_storage_concept__ = "etf_portfolio_configuration"
    __markets_base_identifier__ = "AlpacaETFPortfolioConfiguration"
    __metatable_description__ = (
        "Durable analytical ETF portfolio configurations composed from one signal "
        "configuration, one Alpaca bars configuration, and one rebalance configuration. "
        "Operational Job settings are not duplicated in this table."
    )
    __table_args__ = markets_table_args(
        "AlpacaETFPortfolioConfiguration",
        CheckConstraint("commission_fee >= 0", name="ck_etf_portfolio_commission_fee"),
        CheckConstraint(
            "length(trim(valuation_column)) > 0",
            name="ck_etf_portfolio_valuation_column",
        ),
        CheckConstraint(
            "length(trim(upsample_frequency_id)) > 0",
            name="ck_etf_portfolio_upsample_frequency",
        ),
        CheckConstraint(
            "length(trim(intraday_bar_interpolation_rule)) > 0",
            name="ck_etf_portfolio_interpolation_rule",
        ),
        CheckConstraint(
            "upsample_frequency_id = '1d'",
            name="ck_etf_portfolio_phase_one_upsample_frequency",
        ),
        CheckConstraint(
            "intraday_bar_interpolation_rule = 'ffill'",
            name="ck_etf_portfolio_phase_one_interpolation_rule",
        ),
        CheckConstraint(
            "portfolio_prices_frequency IS NULL OR portfolio_prices_frequency = '1d'",
            name="ck_etf_portfolio_phase_one_prices_frequency",
        ),
        Index(None, "name"),
        Index(None, "signal_configuration_uid"),
        Index(None, "bars_configuration_uid"),
        Index(None, "rebalance_configuration_uid"),
        Index(None, "portfolio_uid", unique=True),
        Index(None, "job_uid", unique=True),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={"label": "Configuration UID", "description": "Stable configuration identity."},
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_configuration_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            f"{SIGNAL_JOB_CONFIGURATION_TABLE_NAME}.uid",
            ondelete="RESTRICT",
        ),
        nullable=False,
        info={
            "label": "Signal Configuration UID",
            "description": "Existing Universe-backed ETF weight Signal configuration.",
        },
    )
    bars_configuration_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AlpacaBarsConfigurationTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Bars Configuration UID",
            "description": "Existing Alpaca bars configuration used as the valuation source.",
        },
    )
    rebalance_configuration_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            f"{PortfolioRebalanceConfigurationTable.__table__.fullname}.uid",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    portfolio_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{PortfolioTable.__table__.fullname}.uid", ondelete="SET NULL"),
        nullable=True,
        info={
            "label": "Portfolio UID",
            "description": "Materialized canonical ms-markets Portfolio identity.",
        },
    )
    job_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        info={
            "label": "Job UID",
            "description": "Dedicated Main Sequence Job; its settings remain Job-owned.",
        },
    )
    upsample_frequency_id: Mapped[str] = mapped_column(String(16), nullable=False)
    intraday_bar_interpolation_rule: Mapped[str] = mapped_column(String(16), nullable=False)
    valuation_column: Mapped[str] = mapped_column(String(64), nullable=False)
    portfolio_prices_frequency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    forward_fill_to_now: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fail_on_missing_prices: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    commission_fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.00018)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class PortfolioRebalanceConfiguration(MarketsMetaTableRow):
    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[PortfolioRebalanceConfigurationTable]] = (
        PortfolioRebalanceConfigurationTable
    )
    __required_tables__: ClassVar[list[type[MarketsBase]]] = [PortfolioRebalanceConfigurationTable]

    name: str
    description: str | None
    strategy: RebalanceStrategy
    created_at: dt.datetime
    updated_at: dt.datetime


class AlpacaETFPortfolioConfiguration(MarketsMetaTableRow):
    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AlpacaETFPortfolioConfigurationTable]] = (
        AlpacaETFPortfolioConfigurationTable
    )
    __required_tables__: ClassVar[list[type[MarketsBase]]] = [
        AlpacaBarsConfigurationTable,
        PortfolioRebalanceConfigurationTable,
        PortfolioTable,
        AlpacaETFPortfolioConfigurationTable,
    ]

    name: str
    description: str | None
    signal_configuration_uid: uuid.UUID
    bars_configuration_uid: uuid.UUID
    rebalance_configuration_uid: uuid.UUID
    portfolio_uid: uuid.UUID | None
    job_uid: uuid.UUID | None
    upsample_frequency_id: str
    intraday_bar_interpolation_rule: str
    valuation_column: str
    portfolio_prices_frequency: str | None
    forward_fill_to_now: bool
    fail_on_missing_prices: bool
    commission_fee: float
    created_at: dt.datetime
    updated_at: dt.datetime


def _name(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty.")
    if len(normalized) > 255:
        raise ValueError(f"{label} must be at most 255 characters.")
    return normalized


def _description(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _nonempty(value: str, *, label: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty.")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be at most {maximum} characters.")
    return normalized


def _phase_one_value(value: str, *, label: str, expected: str, maximum: int) -> str:
    normalized = _nonempty(value, label=label, maximum=maximum)
    if normalized != expected:
        raise ValueError(f"Phase 1 requires {label}={expected!r}.")
    return normalized


def normalize_rebalance_strategy(value: str) -> RebalanceStrategy:
    normalized = str(value or "").strip().lower()
    if normalized not in REBALANCE_STRATEGIES:
        raise ValueError(
            "Phase 1 supports only the immediate_signal analytical rebalance strategy."
        )
    return normalized  # type: ignore[return-value]


def _start_runtime() -> None:
    from src.runtime import application_runtime_models, start_markets_engine

    start_markets_engine(models=application_runtime_models())


def create_rebalance_configuration(
    *,
    name: str,
    strategy: str = "immediate_signal",
    description: str | None = None,
    uid: uuid.UUID | str | None = None,
) -> PortfolioRebalanceConfiguration:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    _start_runtime()
    now = utc_now()
    runtime = resolve_runtime(
        models=[PortfolioRebalanceConfigurationTable],
        row_model_name="PortfolioRebalanceConfiguration",
    )
    result = create_model(
        runtime.context,
        model=PortfolioRebalanceConfigurationTable,
        values={
            "uid": uuid.UUID(str(uid)) if uid is not None else new_markets_uid(),
            "name": _name(name, label="Rebalance configuration name"),
            "description": _description(description),
            "strategy": normalize_rebalance_strategy(strategy),
            "created_at": now,
            "updated_at": now,
        },
    )
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Rebalance configuration create returned no row.")
    return PortfolioRebalanceConfiguration.model_validate(rows[0])


def get_rebalance_configuration(
    configuration_uid: uuid.UUID | str,
) -> PortfolioRebalanceConfiguration | None:
    _start_runtime()
    return PortfolioRebalanceConfiguration.get_by_uid(configuration_uid)


def rebalance_configurations_by_uids(
    configuration_uids: Sequence[uuid.UUID | str],
) -> dict[str, PortfolioRebalanceConfiguration]:
    """Load a rebalance configuration UID set with one governed backend query."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    normalized_uids = list(
        dict.fromkeys(uuid.UUID(str(configuration_uid)) for configuration_uid in configuration_uids)
    )
    if not normalized_uids:
        return {}
    _start_runtime()
    runtime = resolve_runtime(
        models=[PortfolioRebalanceConfigurationTable],
        row_model_name="PortfolioRebalanceConfiguration",
    )
    operation = compile_markets_statement(
        select(PortfolioRebalanceConfigurationTable).where(
            PortfolioRebalanceConfigurationTable.uid.in_(normalized_uids)
        ),
        context=runtime.context,
        operation="select",
        models=[PortfolioRebalanceConfigurationTable],
        access="read",
    )
    rows = [
        PortfolioRebalanceConfiguration.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]
    return {str(row.uid): row for row in rows}


def list_rebalance_configurations(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    ordering: str = "name",
) -> tuple[list[PortfolioRebalanceConfiguration], int]:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    _start_runtime()
    runtime = resolve_runtime(
        models=[PortfolioRebalanceConfigurationTable],
        row_model_name="PortfolioRebalanceConfiguration",
    )
    statement = select(PortfolioRebalanceConfigurationTable)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                PortfolioRebalanceConfigurationTable.name.ilike(pattern),
                PortfolioRebalanceConfigurationTable.description.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(
        statement.order_by(None).subquery()
    )
    descending = ordering.startswith("-")
    key = ordering.removeprefix("-")
    columns = {
        "name": PortfolioRebalanceConfigurationTable.name,
        "updated_at": PortfolioRebalanceConfigurationTable.updated_at,
    }
    if key not in columns:
        raise ValueError(f"Unsupported rebalance configuration ordering {ordering!r}.")
    order = columns[key].desc() if descending else columns[key].asc()
    statement = statement.order_by(order, PortfolioRebalanceConfigurationTable.uid.asc())
    page = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
        models=[PortfolioRebalanceConfigurationTable],
        access="read",
    )
    count = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[PortfolioRebalanceConfigurationTable],
        access="read",
    )
    rows = [
        PortfolioRebalanceConfiguration.model_validate(row)
        for row in operation_result_rows(execute_markets_operation(page, context=runtime.context))
    ]
    count_rows = operation_result_rows(execute_markets_operation(count, context=runtime.context))
    return rows, int(count_rows[0].get("count", 0)) if count_rows else 0


def update_rebalance_configuration(
    configuration_uid: uuid.UUID | str,
    *,
    name: str | None = None,
    description: str | None | object = _UNSET,
    strategy: str | None = None,
) -> PortfolioRebalanceConfiguration:
    current = get_rebalance_configuration(configuration_uid)
    if current is None:
        raise LookupError(f"Rebalance configuration {configuration_uid!s} does not exist.")
    values: dict[str, Any] = {"updated_at": utc_now()}
    if name is not None:
        values["name"] = _name(name, label="Rebalance configuration name")
    if description is not _UNSET:
        values["description"] = _description(description if isinstance(description, str) else None)
    if strategy is not None:
        values["strategy"] = normalize_rebalance_strategy(strategy)
    return PortfolioRebalanceConfiguration.update(configuration_uid, values)


def delete_rebalance_configuration(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    if get_rebalance_configuration(configuration_uid) is None:
        raise LookupError(f"Rebalance configuration {configuration_uid!s} does not exist.")
    return PortfolioRebalanceConfiguration.delete(configuration_uid)


def validate_portfolio_references(
    *,
    signal_configuration_uid: uuid.UUID,
    bars_configuration_uid: uuid.UUID,
    rebalance_configuration_uid: uuid.UUID,
) -> tuple[Any, Any, PortfolioRebalanceConfiguration]:
    from src.market_data import get_bar_configuration
    from src.operations import get_signal_job_configuration

    signal = get_signal_job_configuration(signal_configuration_uid)
    if signal is None:
        raise LookupError(f"Signal configuration {signal_configuration_uid!s} does not exist.")
    bars = get_bar_configuration(bars_configuration_uid)
    if bars is None:
        raise LookupError(f"Bars configuration {bars_configuration_uid!s} does not exist.")
    if not bars.enabled:
        raise ValueError(f"Bars configuration {bars_configuration_uid!s} is disabled.")
    rebalance = get_rebalance_configuration(rebalance_configuration_uid)
    if rebalance is None:
        raise LookupError(
            f"Rebalance configuration {rebalance_configuration_uid!s} does not exist."
        )
    normalize_rebalance_strategy(rebalance.strategy)
    return signal, bars, rebalance


def create_portfolio_configuration_row(
    *,
    name: str,
    signal_configuration_uid: uuid.UUID | str,
    bars_configuration_uid: uuid.UUID | str,
    rebalance_configuration_uid: uuid.UUID | str,
    description: str | None = None,
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
    valuation_column: str = "close",
    portfolio_prices_frequency: str | None = "1d",
    forward_fill_to_now: bool = False,
    fail_on_missing_prices: bool = True,
    commission_fee: float = 0.00018,
    uid: uuid.UUID | str | None = None,
) -> AlpacaETFPortfolioConfiguration:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    signal_uid = uuid.UUID(str(signal_configuration_uid))
    bars_uid = uuid.UUID(str(bars_configuration_uid))
    rebalance_uid = uuid.UUID(str(rebalance_configuration_uid))
    if commission_fee < 0:
        raise ValueError("commission_fee must be greater than or equal to zero.")
    _start_runtime()
    validate_portfolio_references(
        signal_configuration_uid=signal_uid,
        bars_configuration_uid=bars_uid,
        rebalance_configuration_uid=rebalance_uid,
    )
    now = utc_now()
    values = {
        "uid": uuid.UUID(str(uid)) if uid is not None else new_markets_uid(),
        "name": _name(name, label="Portfolio configuration name"),
        "description": _description(description),
        "signal_configuration_uid": signal_uid,
        "bars_configuration_uid": bars_uid,
        "rebalance_configuration_uid": rebalance_uid,
        "portfolio_uid": None,
        "job_uid": None,
        "upsample_frequency_id": _phase_one_value(
            upsample_frequency_id,
            label="upsample_frequency_id",
            expected="1d",
            maximum=16,
        ),
        "intraday_bar_interpolation_rule": _phase_one_value(
            intraday_bar_interpolation_rule,
            label="intraday_bar_interpolation_rule",
            expected="ffill",
            maximum=16,
        ),
        "valuation_column": _nonempty(valuation_column, label="valuation_column", maximum=64),
        "portfolio_prices_frequency": (
            _phase_one_value(
                portfolio_prices_frequency,
                label="portfolio_prices_frequency",
                expected="1d",
                maximum=16,
            )
            if portfolio_prices_frequency is not None
            else None
        ),
        "forward_fill_to_now": bool(forward_fill_to_now),
        "fail_on_missing_prices": bool(fail_on_missing_prices),
        "commission_fee": float(commission_fee),
        "created_at": now,
        "updated_at": now,
    }
    runtime = resolve_runtime(
        models=[AlpacaETFPortfolioConfigurationTable],
        row_model_name="AlpacaETFPortfolioConfiguration",
    )
    result = create_model(
        runtime.context,
        model=AlpacaETFPortfolioConfigurationTable,
        values=values,
    )
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Portfolio configuration create returned no row.")
    return AlpacaETFPortfolioConfiguration.model_validate(rows[0])


def get_portfolio_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaETFPortfolioConfiguration | None:
    _start_runtime()
    return AlpacaETFPortfolioConfiguration.get_by_uid(configuration_uid)


def get_portfolio_configuration_by_job_uid(
    job_uid: uuid.UUID | str,
) -> AlpacaETFPortfolioConfiguration | None:
    _start_runtime()
    rows = AlpacaETFPortfolioConfiguration.filter(job_uid=str(job_uid), limit=2)
    if len(rows) > 1:
        raise RuntimeError(f"Job {job_uid!s} is linked to multiple portfolio configurations.")
    return rows[0] if rows else None


def list_portfolio_configurations(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    signal_configuration_uid: uuid.UUID | str | None = None,
    bars_configuration_uid: uuid.UUID | str | None = None,
    ordering: str = "name",
) -> tuple[list[AlpacaETFPortfolioConfiguration], int]:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    _start_runtime()
    runtime = resolve_runtime(
        models=[AlpacaETFPortfolioConfigurationTable],
        row_model_name="AlpacaETFPortfolioConfiguration",
    )
    statement = select(AlpacaETFPortfolioConfigurationTable)
    if signal_configuration_uid is not None:
        statement = statement.where(
            AlpacaETFPortfolioConfigurationTable.signal_configuration_uid
            == uuid.UUID(str(signal_configuration_uid))
        )
    if bars_configuration_uid is not None:
        statement = statement.where(
            AlpacaETFPortfolioConfigurationTable.bars_configuration_uid
            == uuid.UUID(str(bars_configuration_uid))
        )
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                AlpacaETFPortfolioConfigurationTable.name.ilike(pattern),
                AlpacaETFPortfolioConfigurationTable.description.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(
        statement.order_by(None).subquery()
    )
    descending = ordering.startswith("-")
    key = ordering.removeprefix("-")
    columns = {
        "name": AlpacaETFPortfolioConfigurationTable.name,
        "updated_at": AlpacaETFPortfolioConfigurationTable.updated_at,
    }
    if key not in columns:
        raise ValueError(f"Unsupported portfolio configuration ordering {ordering!r}.")
    order = columns[key].desc() if descending else columns[key].asc()
    statement = statement.order_by(order, AlpacaETFPortfolioConfigurationTable.uid.asc())
    page = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
        models=[AlpacaETFPortfolioConfigurationTable],
        access="read",
    )
    count = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[AlpacaETFPortfolioConfigurationTable],
        access="read",
    )
    rows = [
        AlpacaETFPortfolioConfiguration.model_validate(row)
        for row in operation_result_rows(execute_markets_operation(page, context=runtime.context))
    ]
    count_rows = operation_result_rows(execute_markets_operation(count, context=runtime.context))
    return rows, int(count_rows[0].get("count", 0)) if count_rows else 0


def update_portfolio_configuration_row(
    configuration_uid: uuid.UUID | str,
    *,
    values: dict[str, Any],
) -> AlpacaETFPortfolioConfiguration:
    if get_portfolio_configuration(configuration_uid) is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    return AlpacaETFPortfolioConfiguration.update(
        configuration_uid,
        {**values, "updated_at": utc_now()},
    )


def update_portfolio_calculation_configuration(
    configuration_uid: uuid.UUID | str,
    *,
    name: str | None = None,
    description: str | None | object = _UNSET,
    signal_configuration_uid: uuid.UUID | str | None = None,
    bars_configuration_uid: uuid.UUID | str | None = None,
    rebalance_configuration_uid: uuid.UUID | str | None = None,
    upsample_frequency_id: str | None = None,
    intraday_bar_interpolation_rule: str | None = None,
    valuation_column: str | None = None,
    portfolio_prices_frequency: str | None | object = _UNSET,
    forward_fill_to_now: bool | None = None,
    fail_on_missing_prices: bool | None = None,
    commission_fee: float | None = None,
) -> AlpacaETFPortfolioConfiguration:
    current = get_portfolio_configuration(configuration_uid)
    if current is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    signal_uid = uuid.UUID(str(signal_configuration_uid or current.signal_configuration_uid))
    bars_uid = uuid.UUID(str(bars_configuration_uid or current.bars_configuration_uid))
    rebalance_uid = uuid.UUID(
        str(rebalance_configuration_uid or current.rebalance_configuration_uid)
    )
    validate_portfolio_references(
        signal_configuration_uid=signal_uid,
        bars_configuration_uid=bars_uid,
        rebalance_configuration_uid=rebalance_uid,
    )
    values: dict[str, Any] = {
        "signal_configuration_uid": signal_uid,
        "bars_configuration_uid": bars_uid,
        "rebalance_configuration_uid": rebalance_uid,
    }
    if name is not None:
        values["name"] = _name(name, label="Portfolio configuration name")
    if description is not _UNSET:
        values["description"] = _description(description if isinstance(description, str) else None)
    if upsample_frequency_id is not None:
        values["upsample_frequency_id"] = _phase_one_value(
            upsample_frequency_id,
            label="upsample_frequency_id",
            expected="1d",
            maximum=16,
        )
    if intraday_bar_interpolation_rule is not None:
        values["intraday_bar_interpolation_rule"] = _phase_one_value(
            intraday_bar_interpolation_rule,
            label="intraday_bar_interpolation_rule",
            expected="ffill",
            maximum=16,
        )
    if valuation_column is not None:
        values["valuation_column"] = _nonempty(
            valuation_column, label="valuation_column", maximum=64
        )
    if portfolio_prices_frequency is not _UNSET:
        values["portfolio_prices_frequency"] = (
            _phase_one_value(
                portfolio_prices_frequency,
                label="portfolio_prices_frequency",
                expected="1d",
                maximum=16,
            )
            if isinstance(portfolio_prices_frequency, str)
            else None
        )
    if forward_fill_to_now is not None:
        values["forward_fill_to_now"] = bool(forward_fill_to_now)
    if fail_on_missing_prices is not None:
        values["fail_on_missing_prices"] = bool(fail_on_missing_prices)
    if commission_fee is not None:
        if commission_fee < 0:
            raise ValueError("commission_fee must be greater than or equal to zero.")
        values["commission_fee"] = float(commission_fee)
    return update_portfolio_configuration_row(configuration_uid, values=values)


def delete_portfolio_configuration_row(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    if get_portfolio_configuration(configuration_uid) is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    return AlpacaETFPortfolioConfiguration.delete(configuration_uid)


def project_portfolio_configuration_models() -> list[type[MarketsBase]]:
    return [PortfolioRebalanceConfigurationTable, AlpacaETFPortfolioConfigurationTable]


__all__ = [
    "AlpacaETFPortfolioConfiguration",
    "AlpacaETFPortfolioConfigurationTable",
    "PortfolioRebalanceConfiguration",
    "PortfolioRebalanceConfigurationTable",
    "REBALANCE_STRATEGIES",
    "create_portfolio_configuration_row",
    "create_rebalance_configuration",
    "delete_portfolio_configuration_row",
    "delete_rebalance_configuration",
    "get_portfolio_configuration",
    "get_portfolio_configuration_by_job_uid",
    "get_rebalance_configuration",
    "list_portfolio_configurations",
    "list_rebalance_configurations",
    "normalize_rebalance_strategy",
    "project_portfolio_configuration_models",
    "rebalance_configurations_by_uids",
    "update_portfolio_calculation_configuration",
    "update_portfolio_configuration_row",
    "update_rebalance_configuration",
    "validate_portfolio_references",
]
