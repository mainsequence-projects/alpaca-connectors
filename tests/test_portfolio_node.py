from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import pandas as pd

from src.portfolios.portfolio_node import AlpacaETFPortfolioDataNode


def test_valuation_alignment_reads_the_latest_available_seed() -> None:
    node = object.__new__(AlpacaETFPortfolioDataNode)
    node.valuation_read_start = dt.datetime(2026, 4, 6, 20, tzinfo=dt.UTC)
    source = SimpleNamespace(get_df_between_dates=Mock(return_value="source-frame"))

    def base_alignment(self, *, valuation_source, **kwargs):
        return valuation_source.get_df_between_dates(
            start_date=dt.datetime(2026, 9, 3, 20, tzinfo=dt.UTC),
            end_date=dt.datetime(2026, 9, 4, 20, tzinfo=dt.UTC),
        )

    with patch(
        "msm_portfolios.data_nodes.PortfoliosDataNode._align_valuation_source_to_index",
        autospec=True,
        side_effect=base_alignment,
    ):
        result = node._align_valuation_source_to_index(
            new_index=pd.DatetimeIndex([dt.datetime(2026, 9, 4, 20, tzinfo=dt.UTC)]),
            unique_identifiers=["ALPACA::asset-a"],
            index_freq="1D",
            valuation_source=source,
        )

    assert result == "source-frame"
    source.get_df_between_dates.assert_called_once_with(
        start_date=dt.datetime(2026, 4, 6, 20, tzinfo=dt.UTC),
        end_date=dt.datetime(2026, 9, 4, 20, tzinfo=dt.UTC),
    )


def test_current_portfolio_session_is_a_successful_noop() -> None:
    latest = dt.datetime(2026, 9, 4, 20, tzinfo=dt.UTC)
    node = object.__new__(AlpacaETFPortfolioDataNode)
    node._latest_portfolio_time_index_value = Mock(return_value=latest)
    node._calculate_start_end_dates = Mock(
        return_value=(latest, dt.datetime(2026, 9, 7, 10, tzinfo=dt.UTC))
    )
    node.rebalancer = SimpleNamespace(
        calendar=SimpleNamespace(
            schedule=Mock(
                return_value=pd.DataFrame(
                    {"market_close": [pd.Timestamp(latest)]}
                )
            )
        )
    )
    with (
        patch.object(
            AlpacaETFPortfolioDataNode,
            "logger",
            new_callable=PropertyMock,
            return_value=Mock(),
        ),
        patch(
            "msm_portfolios.data_nodes.PortfoliosDataNode._calculate_portfolio_workflow_values"
        ) as base_calculation,
    ):
        result = node._calculate_portfolio_workflow_values()

    assert result.empty
    assert node._last_canonical_weights_frame.empty
    assert node._last_canonical_portfolio_values_frame.empty
    base_calculation.assert_not_called()


def test_daily_storage_midnight_resolves_to_the_same_sessions_market_close() -> None:
    stored_midnight = dt.datetime(2026, 9, 4, tzinfo=dt.UTC)
    market_close = dt.datetime(2026, 9, 4, 20, tzinfo=dt.UTC)
    node = object.__new__(AlpacaETFPortfolioDataNode)
    node.portfolio_prices_frequency = "1d"
    node.rebalancer = SimpleNamespace(
        calendar=SimpleNamespace(
            schedule=Mock(
                return_value=pd.DataFrame(
                    {"market_close": [pd.Timestamp(market_close)]}
                )
            )
        )
    )

    with patch(
        "msm_portfolios.data_nodes.PortfoliosDataNode._latest_portfolio_time_index_value",
        autospec=True,
        return_value=stored_midnight,
    ):
        result = node._latest_portfolio_time_index_value()

    assert result == market_close
