from __future__ import annotations

import datetime as dt
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import Mock, patch

from etfhextractor.portfolio_publish import required_calendar_window
from etfhextractor.portfolio_signal import ETFHoldingsSignal, ETFHoldingsSignalConfig
from msm_portfolios.data_nodes.signals.storage import SignalWeightsStorage

from src.market_data.alpaca_bars import AlpacaStockBarsNode
from src.market_data.storage import AlpacaStockBars1dSipAllStorage
from src.migrations import METADATA, PROJECT_TABLE_NAMES, migration
from src.portfolios.etf_tracking import (
    AlpacaEtfPortfolioPlan,
    AlpacaEtfTrackingPortfolioConfig,
    ResolvedEtfUniverse,
    build_alpaca_etf_tracking_portfolio,
    build_alpaca_interpolated_prices,
    resolve_alpaca_bars_time_index_meta_table_uid,
)


def test_runtime_dependency_versions_include_shared_http_toolkit() -> None:
    assert version("mainsequence") == "8.1.8"
    assert version("ms-markets") == "1.0.14"


def test_alpaca_bars_node_declares_sdk8_output_table() -> None:
    assert AlpacaStockBarsNode._required_output_table() is AlpacaStockBars1dSipAllStorage
    assert not hasattr(AlpacaStockBarsNode, "_required_storage_table")


def test_external_holdings_signal_uses_current_ms_markets_output_contract() -> None:
    config = ETFHoldingsSignalConfig(etf_ticker="IVV", provider="ishares")

    assert ETFHoldingsSignal._required_output_table() is SignalWeightsStorage
    assert (
        ETFHoldingsSignal.default_config(signal_configuration=config).signal_configuration == config
    )


def test_migration_metadata_includes_fk_dependencies_but_manages_only_project_tables() -> None:
    assert {
        "ms_markets__accountgroup",
        "ms_markets__account",
        "ms_markets__asset",
        "ms_markets__assetcategory",
        "ms_markets__calendar",
        "ms_markets__index",
        "ms_markets__portfolio",
        "ms_markets__signalmetadata",
        *PROJECT_TABLE_NAMES,
    } == set(METADATA.tables)
    assert {model.__table__.name for model in migration.metatable_models} == PROJECT_TABLE_NAMES
    assert {
        table.name
        for table in METADATA.sorted_tables
        if migration.include_name(
            table.name,
            "table",
            {"schema_name": table.schema},
        )
    } == {
        "alpaca_connectors__bars_1d_iex_raw",
        "alpaca_connectors__bars_1d_sip_all",
        "alpaca_connectors__asset_universe",
        "alpaca_connectors__bars_configuration",
        "alpaca_connectors__bars_configuration_asset",
        "alpaca_connectors__acct_alpaca",
        "alpaca_connectors__asset_alpaca",
        "alpaca_connectors__asset_registration_operation",
        "alpaca_connectors__etf_portfolio_configuration",
        "alpaca_connectors__etf_signal_job_configuration",
        "alpaca_connectors__portfolio_rebalance_configuration",
        "alpaca_connectors__universe_source",
    }


def test_required_calendar_window_includes_backtest_and_operational_buffers() -> None:
    start, end = required_calendar_window(
        backtest_start_days=60,
        today=dt.date(2026, 9, 2),
    )

    assert start == dt.date(2026, 6, 4)
    assert end == dt.date(2027, 9, 3)


def test_portfolio_price_source_resolves_the_migration_catalog_identity() -> None:
    output_table = SimpleNamespace(
        uid="121d5cee-431d-474d-8cc0-7d2a0020124d",
        identifier=AlpacaStockBars1dSipAllStorage.__table__.name,
        namespace=AlpacaStockBars1dSipAllStorage.__metatable_namespace__,
        physical_table_name=AlpacaStockBars1dSipAllStorage.__table__.name,
    )

    with patch(
        "mainsequence.client.TimeIndexMetaTable.filter_by_body",
        return_value=[output_table],
    ) as filter_by_body:
        table_uid = resolve_alpaca_bars_time_index_meta_table_uid()

    assert table_uid == output_table.uid
    filter_by_body.assert_called_once_with(
        identifier__in=[AlpacaStockBars1dSipAllStorage.__table__.name],
        namespace__in=[AlpacaStockBars1dSipAllStorage.__metatable_namespace__],
        physical_table_name__in=[AlpacaStockBars1dSipAllStorage.__table__.name],
        limit=2,
    )


def test_interpolated_prices_receive_explicit_asset_calendar_scope() -> None:
    node = object()

    with patch(
        "msm_portfolios.contrib.prices.data_nodes.InterpolatedPrices",
        return_value=node,
    ) as interpolated_prices:
        result = build_alpaca_interpolated_prices(
            source_time_index_meta_table_uid="source-table-uid",
            asset_identifiers=["ALPACA::asset-b", "ALPACA::asset-a", "ALPACA::asset-a"],
            calendar_identifier="NYSE",
        )

    assert result is node
    interpolation_config = interpolated_prices.call_args.kwargs["interpolation_config"]
    assert interpolation_config.asset_list == [
        {
            "asset_identifier": "ALPACA::asset-a",
            "calendar": "NYSE",
        },
        {
            "asset_identifier": "ALPACA::asset-b",
            "calendar": "NYSE",
        },
    ]


def test_alpaca_portfolio_uses_connector_owned_universe_signal() -> None:
    config = AlpacaEtfTrackingPortfolioConfig(
        universe_uid="universe-uid",
        account_uid="account-uid",
    )
    universe_run_plan = object()
    plan = AlpacaEtfPortfolioPlan(
        config=config,
        universe=ResolvedEtfUniverse(
            universe_uid="universe-uid",
            source_uid="source-uid",
            asset_category_uid="category-uid",
            etf_ticker="IVV",
            provider="ishares",
            component_weights_by_symbol={"AAPL": 1.0},
            asset_identifiers_by_symbol={"AAPL": "ALPACA::asset-uuid"},
        ),
        universe_run_plan=universe_run_plan,
        source_time_index_meta_table_uid="source-table-uid",
        source_storage_identifier="bars-storage",
        source_storage_table_name="bars-table",
        interpolated_storage_identifier="interpolated-storage",
        portfolio_unique_identifier="IVV_TRACKER__ALPACA",
    )
    valuation_source = object()
    calendar_row = SimpleNamespace(uid="calendar-uid", unique_identifier="NYSE")
    portfolio_row = SimpleNamespace(uid="portfolio-uid")
    signal = SimpleNamespace(signal_uid="signal-uid")
    calendar_events = Mock()
    portfolio_node = Mock()

    with (
        patch("src.runtime.start_portfolio_markets_engine") as start_engine,
        patch(
            "src.portfolios.etf_tracking.plan_alpaca_etf_tracking_portfolio",
            return_value=plan,
        ),
        patch(
            "src.portfolios.etf_tracking.build_alpaca_interpolated_prices",
            return_value=valuation_source,
        ) as build_prices,
        patch(
            "src.portfolios.etf_tracking.build_alpaca_etf_holdings_signal",
            return_value=signal,
        ) as build_signal,
        patch(
            "src.portfolios.etf_tracking.ensure_trading_calendar",
            return_value=calendar_row,
        ),
        patch("msm.api.portfolios.Portfolio.upsert", return_value=portfolio_row),
        patch(
            "msm_portfolios.configuration.PortfolioConfiguration",
            return_value="portfolio-configuration",
        ),
        patch("msm_portfolios.configuration.PortfolioBuildConfiguration"),
        patch("msm_portfolios.configuration.PortfolioExecutionConfiguration"),
        patch("msm_portfolios.configuration.BacktestingWeightsConfig"),
        patch("msm_portfolios.configuration.PortfolioMarketsConfig"),
        patch("msm_portfolios.configuration.FrontEndDetails"),
        patch("msm_portfolios.rebalance_strategy.CalendarEventSignal"),
        patch(
            "msm_portfolios.data_nodes.PortfolioCalendarEvents",
            return_value=calendar_events,
        ),
        patch("msm_portfolios.data_nodes.PortfolioCalendarEventsConfiguration"),
        patch("msm_portfolios.data_nodes.PortfoliosDataNode", return_value=portfolio_node),
    ):
        result = build_alpaca_etf_tracking_portfolio(config, run=False)

    start_engine.assert_called_once_with()
    build_signal.assert_called_once_with(
        universe_uid="universe-uid",
        account_uid="account-uid",
        prepared_plan=universe_run_plan,
    )
    build_prices.assert_called_once_with(
        source_time_index_meta_table_uid="source-table-uid",
        asset_identifiers=["ALPACA::asset-uuid"],
        calendar_identifier="NYSE",
        upsample_frequency_id="1d",
        intraday_bar_interpolation_rule="ffill",
    )
    assert result.calendar_row is calendar_row
    assert result.portfolio_row is portfolio_row
    assert result.signal is signal
    assert result.calendar_events is calendar_events
    assert result.portfolio_node is portfolio_node
