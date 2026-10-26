from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from msm.repositories.base import MarketsRepositoryContext

from alpaca_connectors.portfolios.portfolio_history import (
    PortfolioValueObservation,
    calculate_portfolio_performance,
    read_portfolio_history,
)


def _markets_rows(*, portfolio_exists: bool = True):
    """Patches for the Markets Portfolio, Calendar and PortfolioMetadata row reads."""
    from msm.api.calendars import Calendar
    from msm.api.portfolios import Portfolio
    from msm_portfolios.api import PortfolioMetadata

    portfolio = SimpleNamespace(
        unique_identifier="ALPACA_ETF_PORTFOLIO__ABC",
        calendar_uid="calendar-uid",
        backtest_table_price_column_name="close",
    )
    calendar = SimpleNamespace(
        display_name="US equities",
        calendar_type="exchange",
        timezone="America/New_York",
        valid_from=dt.date(2018, 1, 1),
        valid_to=dt.date(2027, 1, 1),
    )
    return (
        patch.object(
            Portfolio,
            "get_by_unique_identifier",
            classmethod(lambda cls, value: portfolio if portfolio_exists else None),
        ),
        patch.object(Calendar, "get_by_uid", classmethod(lambda cls, uid: calendar)),
        patch.object(
            PortfolioMetadata,
            "filter",
            classmethod(
                lambda cls, **kw: [SimpleNamespace(description="Daily analytical ETF portfolio.")]
            ),
        ),
    )


def test_portfolio_history_reads_markets_metadata_and_own_values_in_one_query() -> None:
    older = dt.datetime(2026, 9, 3, 20, tzinfo=dt.UTC)
    newer = dt.datetime(2026, 9, 4, 20, tzinfo=dt.UTC)
    rows = [
        {
            "time_index": newer,
            "close": 102.5,
            "period_return": 0.025,
            "calculated_close": 102.5,
            "close_time": newer,
            "total_observation_count": 2,
        },
        {
            "time_index": older,
            "close": 100.0,
            "period_return": 0.0,
            "calculated_close": 100.0,
            "close_time": older,
            "total_observation_count": 2,
        },
    ]
    context = MarketsRepositoryContext()
    portfolio_patch, calendar_patch, metadata_patch = _markets_rows()

    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=context),
        ),
        patch(
            "alpaca_connectors.portfolios.execution.portfolio_unique_identifier",
            return_value="ALPACA_ETF_PORTFOLIO__ABC",
        ),
        portfolio_patch,
        calendar_patch,
        metadata_patch,
        patch(
            "msm.repositories.base.compile_markets_statement",
            return_value="portfolio-history-query",
        ) as compile_statement,
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={"rows": rows, "truncated": False},
        ) as execute_operation,
    ):
        history = read_portfolio_history(configuration_uid="config-uid", observation_limit=100)

    assert history.materialized is True
    assert history.description == "Daily analytical ETF portfolio."
    assert history.calendar_name == "US equities"
    assert history.calendar_timezone == "America/New_York"
    assert history.backtest_price_column == "close"
    assert [item.time_index for item in history.observations] == [older, newer]
    assert [item.close for item in history.observations] == [100.0, 102.5]
    assert history.total_observation_count == 2
    assert history.observations[-1].cumulative_return == pytest.approx(0.025)
    assert history.observations[-1].drawdown == 0.0
    assert history.performance is not None
    assert history.performance.methodology == "empyrical-reloaded"
    assert history.performance.total_return == pytest.approx(0.025)
    compile_statement.assert_called_once()
    compiled_sql = str(compile_statement.call_args.args[0])
    assert "alpaca_connectors__portfoliosts__main" in compiled_sql
    assert "ms_markets__" not in compiled_sql
    assert "ORDER BY" in compiled_sql
    assert "LIMIT" in compiled_sql
    execute_operation.assert_called_once_with("portfolio-history-query", context=context)


def test_portfolio_history_reports_an_unmaterialized_portfolio() -> None:
    context = MarketsRepositoryContext()
    portfolio_patch, calendar_patch, metadata_patch = _markets_rows(portfolio_exists=False)
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=context),
        ),
        patch(
            "alpaca_connectors.portfolios.execution.portfolio_unique_identifier",
            return_value="ALPACA_ETF_PORTFOLIO__ABC",
        ),
        portfolio_patch,
        calendar_patch,
        metadata_patch,
        patch("msm.repositories.base.compile_markets_statement") as compile_statement,
    ):
        history = read_portfolio_history(configuration_uid="config-uid")

    assert history.materialized is False
    assert history.observations == ()
    compile_statement.assert_not_called()


@pytest.mark.parametrize("limit", [0, 5_001, True])
def test_portfolio_history_rejects_invalid_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="observation_limit"):
        read_portfolio_history(configuration_uid="config-uid", observation_limit=limit)


def test_portfolio_performance_calculates_standard_daily_statistics() -> None:
    timestamps = [dt.datetime(2026, 9, day, 20, tzinfo=dt.UTC) for day in (1, 2, 3)]
    observations = tuple(
        PortfolioValueObservation(
            time_index=time_index,
            close=value,
            period_return=None,
            calculated_close=value,
            close_time=time_index,
        )
        for time_index, value in zip(timestamps, (100.0, 110.0, 99.0), strict=True)
    )

    annotated, performance = calculate_portfolio_performance(observations)

    assert performance.return_observation_count == 2
    assert performance.total_return == pytest.approx(-0.01)
    assert performance.max_drawdown == pytest.approx(-0.1)
    assert performance.best_period_return == pytest.approx(0.1)
    assert performance.worst_period_return == pytest.approx(-0.1)
    assert performance.positive_period_ratio == pytest.approx(0.5)
    assert performance.annualized_volatility is not None
    assert performance.sharpe_ratio is not None
    assert annotated[-1].cumulative_return == pytest.approx(-0.01)
    assert annotated[-1].drawdown == pytest.approx(-0.1)
