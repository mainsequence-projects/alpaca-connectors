from __future__ import annotations

from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import patch

from packaging.specifiers import SpecifierSet

from alpaca_connectors.market_data.alpaca_bars import AlpacaStockBarsNode
from alpaca_connectors.market_data.storage import AlpacaStockBars1dSipAllStorage
from alpaca_connectors.migrations import METADATA, PROJECT_TABLE_NAMES, migration
from alpaca_connectors.portfolios.interpolated_prices import (
    build_alpaca_interpolated_prices,
    resolve_alpaca_bars_time_index_meta_table_uid,
)


def test_runtime_dependency_versions_match_the_sdk9_metatables_stack() -> None:
    assert version("mainsequence") in SpecifierSet(">=9.0.2,<10")
    assert version("mainsequence-metatable") in SpecifierSet(">=0.1.6,<0.2")
    assert version("ms-markets") in SpecifierSet(">=4.0.1,<5")


def test_alpaca_bars_node_declares_sdk8_output_table() -> None:
    assert AlpacaStockBarsNode._required_output_table() is AlpacaStockBars1dSipAllStorage
    assert not hasattr(AlpacaStockBarsNode, "_required_storage_table")


def test_migration_metadata_includes_fk_dependencies_but_manages_only_project_tables() -> None:
    assert {
        "ms_markets__accountgroup",
        "ms_markets__account",
        "ms_markets__asset",
        "ms_markets__calendar",
        "ms_markets__index",
        "ms_markets__portfolio",
        "ms_markets__signalmetadata",
        "ms_markets__ordermanager",
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
        "alpaca_connectors__bars_configuration",
        "alpaca_connectors__bars_configuration_asset",
        "alpaca_connectors__signal_asset",
        "alpaca_connectors__acct_alpaca",
        "alpaca_connectors__account_key_request",
        "alpaca_connectors__account_sync_operation",
        "alpaca_connectors__asset_alpaca",
        "alpaca_connectors__asset_registration_operation",
        "alpaca_connectors__portfolio_configuration",
        "alpaca_connectors__portfolio_rebalance_configuration",
        "alpaca_connectors__signal_definition",
        "alpaca_connectors__ended_portfolio",
        # Contract instances of the Markets time series (ms-markets ADR 0047).
        "alpaca_connectors__signalweightsts__main",
        "alpaca_connectors__portfoliosts__main",
        "alpaca_connectors__portfolioweightsts__main",
        "alpaca_connectors__portfoliorebalancestatets__main",
        "alpaca_connectors__portfoliocalendareventsts__main",
        "alpaca_connectors__ordersts__main",
        "alpaca_connectors__tradests__main",
    }


def test_portfolio_price_source_resolves_the_migration_catalog_identity() -> None:
    output_table = SimpleNamespace(
        uid="121d5cee-431d-474d-8cc0-7d2a0020124d",
        identifier=AlpacaStockBars1dSipAllStorage.__table__.name,
        namespace=AlpacaStockBars1dSipAllStorage.__metatable_namespace__,
        physical_table_name=AlpacaStockBars1dSipAllStorage.__table__.name,
    )

    with patch(
        "metatables.TimeIndexMetaTable.filter_by_body",
        return_value=[output_table],
    ) as filter_by_body:
        table_uid = resolve_alpaca_bars_time_index_meta_table_uid()

    assert table_uid == output_table.uid
    filter_by_body.assert_called_once_with(
        physical_table_name__in=[AlpacaStockBars1dSipAllStorage.__table__.name],
        limit=2,
    )


def test_interpolated_prices_scope_is_identifiers_and_bars_follow_each_asset_calendar() -> None:
    node = object()

    with patch(
        "msm_portfolios.contrib.prices.updaters.InterpolatedPrices",
        return_value=node,
    ) as interpolated_prices:
        result = build_alpaca_interpolated_prices(
            source_time_index_meta_table_uid="source-table-uid",
            asset_identifiers=["ALPACA::asset-b", "ALPACA::asset-a", "ALPACA::asset-a"],
        )

    assert result is node
    # ms-markets 4 refuses a calendar in the scope; each asset's bars follow the stored calendar
    # registration linked it to (ADR 0026).
    interpolation_config = interpolated_prices.call_args.kwargs["interpolation_config"]
    assert interpolation_config.asset_list == ["ALPACA::asset-a", "ALPACA::asset-b"]
