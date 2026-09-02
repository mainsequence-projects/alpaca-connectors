from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

from etfhextractor.portfolio_publish import required_calendar_window
from etfhextractor.portfolio_signal import ETFHoldingsSignal, ETFHoldingsSignalConfig
from msm_portfolios.data_nodes.signals.storage import SignalWeightsStorage

from src.data_nodes.alpaca_bars import AlpacaStockBarsNode
from src.markets_storage.alpaca_bars import AlpacaStockBars1dSipAllStorage
from src.migrations import METADATA, PROJECT_TABLE_NAMES, migration
from src.portfolios.etf_tracking import (
    AlpacaEtfPortfolioPlan,
    AlpacaEtfTrackingPortfolioConfig,
    ResolvedEtfUniverse,
    build_alpaca_etf_tracking_portfolio,
    resolve_alpaca_bars_time_index_meta_table_uid,
)


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
        *PROJECT_TABLE_NAMES,
    } == set(METADATA.tables)
    assert {model.__table__.name for model in migration.metatable_models} == PROJECT_TABLE_NAMES
    assert [
        table.name
        for table in METADATA.sorted_tables
        if migration.include_name(
            table.name,
            "table",
            {"schema_name": table.schema},
        )
    ] == [
        "alpaca_connectors__bars_1d_iex_raw",
        "alpaca_connectors__bars_1d_sip_all",
        "alpaca_connectors__acct_alpaca",
    ]


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


def test_alpaca_portfolio_delegates_graph_build_to_etfhextractor() -> None:
    config = AlpacaEtfTrackingPortfolioConfig(etf_ticker="IVV")
    plan = AlpacaEtfPortfolioPlan(
        config=config,
        universe=ResolvedEtfUniverse(
            etf_ticker="IVV",
            provider="ishares",
            component_weights_by_symbol={"AAPL": 1.0},
            asset_identifiers_by_symbol={"AAPL": "BBG000B9XRY4"},
        ),
        source_time_index_meta_table_uid="source-table-uid",
        source_storage_identifier="bars-storage",
        source_storage_table_name="bars-table",
        interpolated_storage_identifier="interpolated-storage",
        portfolio_unique_identifier="IVV_TRACKER__ALPACA",
    )
    valuation_source = object()
    calendar_row = SimpleNamespace(uid="calendar-uid")
    portfolio_row = SimpleNamespace(uid="portfolio-uid")
    signal = SimpleNamespace(signal_uid="signal-uid")
    portfolio_node = object()
    external_result = SimpleNamespace(
        calendar_row=calendar_row,
        portfolio_row=portfolio_row,
        signal=signal,
        valuation_source=valuation_source,
        portfolio_node=portfolio_node,
        run_result=None,
    )

    with (
        patch("src.runtime.start_portfolio_markets_engine") as start_engine,
        patch(
            "src.portfolios.etf_tracking.plan_alpaca_etf_tracking_portfolio",
            return_value=plan,
        ),
        patch(
            "src.portfolios.etf_tracking.build_alpaca_interpolated_prices",
            return_value=valuation_source,
        ),
        patch(
            "src.portfolios.etf_tracking.build_etf_tracking_portfolio",
            return_value=external_result,
        ) as external_build,
    ):
        result = build_alpaca_etf_tracking_portfolio(config, run=False)

    start_engine.assert_called_once_with()
    kwargs = external_build.call_args.kwargs
    assert kwargs["price_source_instance"] is valuation_source
    assert kwargs["portfolio_unique_identifier"] == "IVV_TRACKER__ALPACA"
    assert kwargs["asset_identifiers"] == ["BBG000B9XRY4"]
    assert kwargs["allowed_asset_classes"] == ("Equity",)
    assert kwargs["start_engine"] is False
    assert result.calendar_row is calendar_row
    assert result.portfolio_row is portfolio_row
    assert result.signal is signal
    assert result.portfolio_node is portfolio_node
