"""Read canonical ms-markets portfolio metadata and value observations."""

from __future__ import annotations

import datetime as dt
import math
import warnings
from dataclasses import dataclass
from typing import Any

import empyrical as emp
import numpy as np

MAX_PORTFOLIO_OBSERVATIONS = 5_000
PORTFOLIO_HISTORY_QUERY_TIMEOUT_MS = 30_000
PORTFOLIO_ANNUALIZATION_FACTOR = 252
PORTFOLIO_RISK_FREE_RATE = 0.0


@dataclass(frozen=True, slots=True)
class PortfolioValueObservation:
    """One canonical portfolio value observation."""

    time_index: dt.datetime
    close: float | None
    period_return: float | None
    calculated_close: float | None
    close_time: dt.datetime | None
    cumulative_return: float | None = None
    drawdown: float | None = None


@dataclass(frozen=True, slots=True)
class PortfolioPerformance:
    """Window-scoped daily performance statistics calculated with Empyrical."""

    methodology: str
    frequency: str
    annualization_factor: int
    risk_free_rate: float
    observation_count: int
    return_observation_count: int
    period_start: dt.datetime | None
    period_end: dt.datetime | None
    total_return: float | None
    annualized_return: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    max_drawdown: float | None
    calmar_ratio: float | None
    best_period_return: float | None
    worst_period_return: float | None
    positive_period_ratio: float | None


@dataclass(frozen=True, slots=True)
class PortfolioHistory:
    """Canonical portfolio metadata plus its latest value observations."""

    materialized: bool
    portfolio_identifier: str
    description: str | None
    calendar_name: str | None
    calendar_type: str | None
    calendar_timezone: str | None
    calendar_valid_from: dt.date | None
    calendar_valid_to: dt.date | None
    backtest_price_column: str | None
    observations: tuple[PortfolioValueObservation, ...]
    total_observation_count: int = 0
    performance: PortfolioPerformance | None = None


def _normalize_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("observation_limit must be an integer.")
    if value < 1 or value > MAX_PORTFOLIO_OBSERVATIONS:
        raise ValueError(
            f"observation_limit must be between 1 and {MAX_PORTFOLIO_OBSERVATIONS}."
        )
    return value


def _normalize_datetime(value: Any, *, field_name: str) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"Portfolio storage returned {field_name} without a timezone.")
    return parsed.astimezone(dt.UTC)


def _normalize_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    return dt.date.fromisoformat(text) if text else None


def _normalize_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not math.isfinite(normalized):
        raise RuntimeError(f"Portfolio storage returned a non-finite {field_name}.")
    return normalized


def _observation_value(observation: PortfolioValueObservation) -> float | None:
    return observation.close if observation.close is not None else observation.calculated_close


def _finite_metric(value: Any) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def calculate_portfolio_performance(
    observations: tuple[PortfolioValueObservation, ...],
) -> tuple[tuple[PortfolioValueObservation, ...], PortfolioPerformance]:
    """Annotate the value series and calculate standard daily return/risk statistics."""
    annotated: list[PortfolioValueObservation] = []
    first_value: float | None = None
    running_peak: float | None = None
    previous_value: float | None = None
    returns: list[float] = []

    for observation in observations:
        value = _observation_value(observation)
        cumulative_return: float | None = None
        drawdown: float | None = None
        if annotated:
            if observation.period_return is not None:
                returns.append(observation.period_return)
            elif value is not None and previous_value is not None and previous_value != 0:
                returns.append(value / previous_value - 1.0)

        if value is not None:
            if first_value is None and value != 0:
                first_value = value
            if first_value is not None:
                cumulative_return = value / first_value - 1.0
            running_peak = value if running_peak is None else max(running_peak, value)
            if running_peak != 0:
                drawdown = value / running_peak - 1.0

            previous_value = value

        annotated.append(
            PortfolioValueObservation(
                time_index=observation.time_index,
                close=observation.close,
                period_return=observation.period_return,
                calculated_close=observation.calculated_close,
                close_time=observation.close_time,
                cumulative_return=cumulative_return,
                drawdown=drawdown,
            )
        )

    returns_array = np.asarray(returns, dtype=float)
    common = {
        "methodology": "empyrical-reloaded",
        "frequency": "daily",
        "annualization_factor": PORTFOLIO_ANNUALIZATION_FACTOR,
        "risk_free_rate": PORTFOLIO_RISK_FREE_RATE,
        "observation_count": len(observations),
        "return_observation_count": len(returns),
        "period_start": observations[0].time_index if observations else None,
        "period_end": observations[-1].time_index if observations else None,
    }
    if returns_array.size == 0:
        return tuple(annotated), PortfolioPerformance(
            **common,
            total_return=None,
            annualized_return=None,
            annualized_volatility=None,
            sharpe_ratio=None,
            sortino_ratio=None,
            max_drawdown=None,
            calmar_ratio=None,
            best_period_return=None,
            worst_period_return=None,
            positive_period_ratio=None,
        )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        total_return = emp.cum_returns_final(returns_array)
        annualized_return = emp.annual_return(
            returns_array,
            annualization=PORTFOLIO_ANNUALIZATION_FACTOR,
        )
        maximum_drawdown = emp.max_drawdown(returns_array)
        calmar_ratio = emp.calmar_ratio(
            returns_array,
            annualization=PORTFOLIO_ANNUALIZATION_FACTOR,
        )
        if returns_array.size >= 2:
            annualized_volatility = emp.annual_volatility(
                returns_array,
                annualization=PORTFOLIO_ANNUALIZATION_FACTOR,
            )
            sharpe_ratio = emp.sharpe_ratio(
                returns_array,
                risk_free=PORTFOLIO_RISK_FREE_RATE,
                annualization=PORTFOLIO_ANNUALIZATION_FACTOR,
            )
            sortino_ratio = emp.sortino_ratio(
                returns_array,
                required_return=PORTFOLIO_RISK_FREE_RATE,
                annualization=PORTFOLIO_ANNUALIZATION_FACTOR,
            )
        else:
            annualized_volatility = None
            sharpe_ratio = None
            sortino_ratio = None

    return tuple(annotated), PortfolioPerformance(
        **common,
        total_return=_finite_metric(total_return),
        annualized_return=_finite_metric(annualized_return),
        annualized_volatility=_finite_metric(annualized_volatility),
        sharpe_ratio=_finite_metric(sharpe_ratio),
        sortino_ratio=_finite_metric(sortino_ratio),
        max_drawdown=_finite_metric(maximum_drawdown),
        calmar_ratio=_finite_metric(calmar_ratio),
        best_period_return=float(np.max(returns_array)),
        worst_period_return=float(np.min(returns_array)),
        positive_period_ratio=float(np.count_nonzero(returns_array > 0) / returns_array.size),
    )


def _history_from_rows(
    *,
    portfolio_identifier: str,
    rows: list[dict[str, Any]],
) -> PortfolioHistory:
    if not rows:
        observations, performance = calculate_portfolio_performance(())
        return PortfolioHistory(
            materialized=False,
            portfolio_identifier=portfolio_identifier,
            description=None,
            calendar_name=None,
            calendar_type=None,
            calendar_timezone=None,
            calendar_valid_from=None,
            calendar_valid_to=None,
            backtest_price_column=None,
            observations=observations,
            total_observation_count=0,
            performance=performance,
        )

    first = rows[0]
    observations: list[PortfolioValueObservation] = []
    seen_time_indexes: set[dt.datetime] = set()
    for row in reversed(rows):
        time_index = _normalize_datetime(row.get("time_index"), field_name="time_index")
        if time_index is None:
            continue
        if time_index in seen_time_indexes:
            raise RuntimeError(
                "Portfolio storage returned duplicate rows at " f"{time_index.isoformat()}."
            )
        seen_time_indexes.add(time_index)
        observations.append(
            PortfolioValueObservation(
                time_index=time_index,
                close=_normalize_float(row.get("close"), field_name="close"),
                period_return=_normalize_float(
                    row.get("period_return"), field_name="period_return"
                ),
                calculated_close=_normalize_float(
                    row.get("calculated_close"), field_name="calculated_close"
                ),
                close_time=_normalize_datetime(row.get("close_time"), field_name="close_time"),
            )
        )
    annotated_observations, performance = calculate_portfolio_performance(tuple(observations))
    total_observation_count = max(
        int(first.get("total_observation_count") or 0),
        len(annotated_observations),
    )
    return PortfolioHistory(
        materialized=True,
        portfolio_identifier=portfolio_identifier,
        description=str(first.get("portfolio_description") or "").strip() or None,
        calendar_name=str(first.get("calendar_name") or "").strip() or None,
        calendar_type=str(first.get("calendar_type") or "").strip() or None,
        calendar_timezone=str(first.get("calendar_timezone") or "").strip() or None,
        calendar_valid_from=_normalize_date(first.get("calendar_valid_from")),
        calendar_valid_to=_normalize_date(first.get("calendar_valid_to")),
        backtest_price_column=(
            str(first.get("backtest_price_column") or "").strip() or None
        ),
        observations=annotated_observations,
        total_observation_count=total_observation_count,
        performance=performance,
    )


def read_portfolio_history(
    *,
    configuration_uid: Any,
    observation_limit: int = 100,
) -> PortfolioHistory:
    """Read one portfolio's canonical metadata and latest value observations.

    The stable configuration-derived portfolio identifier scopes the shared
    ``PortfoliosStorage`` table. The bounded query loads portfolio metadata and the latest
    observations together, without requiring a DataNode execution or scanning other portfolios.
    """
    from msm.api.base import operation_result_rows
    from msm.models import CalendarTable, PortfolioTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from msm_portfolios.data_nodes.portfolios.storage import PortfoliosStorage
    from msm_portfolios.models import PortfolioMetadataTable
    from sqlalchemy import func, select

    from src.portfolios.execution import portfolio_unique_identifier
    from src.runtime import start_markets_engine

    normalized_limit = _normalize_limit(observation_limit)
    portfolio_identifier = portfolio_unique_identifier(configuration_uid)
    runtime = start_markets_engine()
    statement = (
        select(
            PortfolioTable.unique_identifier.label("portfolio_identifier"),
            PortfolioTable.backtest_table_price_column_name.label(
                "backtest_price_column"
            ),
            PortfolioMetadataTable.description.label("portfolio_description"),
            CalendarTable.display_name.label("calendar_name"),
            CalendarTable.calendar_type.label("calendar_type"),
            CalendarTable.timezone.label("calendar_timezone"),
            CalendarTable.valid_from.label("calendar_valid_from"),
            CalendarTable.valid_to.label("calendar_valid_to"),
            PortfoliosStorage.time_index.label("time_index"),
            PortfoliosStorage.close.label("close"),
            PortfoliosStorage.return_.label("period_return"),
            PortfoliosStorage.calculated_close.label("calculated_close"),
            PortfoliosStorage.close_time.label("close_time"),
            func.count(PortfoliosStorage.time_index)
            .over()
            .label("total_observation_count"),
        )
        .select_from(PortfolioTable)
        .join(CalendarTable, CalendarTable.uid == PortfolioTable.calendar_uid)
        .outerjoin(
            PortfolioMetadataTable,
            PortfolioMetadataTable.unique_identifier == PortfolioTable.unique_identifier,
        )
        .outerjoin(
            PortfoliosStorage,
            PortfoliosStorage.portfolio_identifier == PortfolioTable.unique_identifier,
        )
        .where(PortfolioTable.unique_identifier == portfolio_identifier)
        .order_by(PortfoliosStorage.time_index.desc())
        .limit(normalized_limit)
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
        models=[
            PortfolioTable,
            PortfolioMetadataTable,
            CalendarTable,
            PortfoliosStorage,
        ],
        access="read",
    )
    result = execute_markets_operation(operation, context=runtime.context)
    return _history_from_rows(
        portfolio_identifier=portfolio_identifier,
        rows=operation_result_rows(result),
    )


__all__ = [
    "MAX_PORTFOLIO_OBSERVATIONS",
    "PORTFOLIO_ANNUALIZATION_FACTOR",
    "PORTFOLIO_RISK_FREE_RATE",
    "PortfolioHistory",
    "PortfolioPerformance",
    "PortfolioValueObservation",
    "calculate_portfolio_performance",
    "read_portfolio_history",
]
