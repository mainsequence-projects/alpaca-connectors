from __future__ import annotations

from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import patch

from packaging.specifiers import SpecifierSet

from alpaca_connectors.base import ProjectBase
from alpaca_connectors.instances import INTERPOLATED_PRICES_BY_PROFILE
from alpaca_connectors.market_data.alpaca_bars import AlpacaStockBarsNode
from alpaca_connectors.market_data.storage import AlpacaStockBars1dSipAllStorage
from alpaca_connectors.migrations import METADATA, PROJECT_TABLE_NAMES, migration
from alpaca_connectors.portfolios.interpolated_prices import (
    build_alpaca_interpolated_prices,
    resolve_alpaca_bars_time_index_meta_table_uid,
)


def test_runtime_dependency_versions_match_the_sdk9_metatables_stack() -> None:
    assert version("mainsequence") in SpecifierSet(">=9.0.38,<10")
    assert version("mainsequence-metatable") in SpecifierSet(">=0.1.77,<0.2")
    assert version("ms-markets") in SpecifierSet(">=5.3.0,<6")


def test_alpaca_bars_node_declares_sdk8_output_table() -> None:
    assert AlpacaStockBarsNode._required_output_table() is AlpacaStockBars1dSipAllStorage
    assert not hasattr(AlpacaStockBarsNode, "_required_storage_table")


def test_migration_metadata_holds_only_this_applications_tables() -> None:
    # ms-markets 5.0.0 (ADR 0029, decision 5): every table is on ProjectBase and keys to Markets by
    # Markets' own column objects, so the provider's metadata holds no Markets table and the
    # provider needs no include hook.
    assert METADATA is ProjectBase.metadata
    assert set(METADATA.tables) == PROJECT_TABLE_NAMES
    assert migration.include_name_hook is None
    assert migration.include_object_hook is None
    for model in migration.metatable_models:
        assert model.metadata is ProjectBase.metadata, model.__table__.name
    assert {model.__table__.name for model in migration.metatable_models} == PROJECT_TABLE_NAMES
    assert PROJECT_TABLE_NAMES == {
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
        # Private contract instances of the Markets time series (ms-markets ADR 0047); signal
        # weights, portfolio weights and values are Markets' published series (ADR 0031).
        "alpaca_connectors__portfoliorebalancestatets__main",
        "alpaca_connectors__portfoliocalendareventsts__main",
        "alpaca_connectors__ordersts__main",
        "alpaca_connectors__tradests__main",
        # One interpolated-price table per bars profile, revision 0034 (ADR 0029).
        "alpaca_connectors__interpolatedpricests__t_1330c8b4c5fd5124",
        "alpaca_connectors__interpolatedpricests__ff97574884b78031",
    }
    # The foreign keys reach Markets' registries through their own tables, outside this metadata.
    assert {
        key.column.table.name
        for table in METADATA.tables.values()
        for key in table.foreign_keys
        if key.column.table.name not in PROJECT_TABLE_NAMES
    } == {
        "ms_markets__account",
        "ms_markets__asset",
        "ms_markets__calendar",
        "ms_markets__ordermanager",
        "ms_markets__portfolio",
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
            profile=("1d", "iex", "raw"),
        )

    assert result is node
    # ms-markets 4 refuses a calendar in the scope; each asset's bars follow the stored calendar
    # registration linked it to (ADR 0026).
    interpolation_config = interpolated_prices.call_args.kwargs["interpolation_config"]
    assert interpolation_config.asset_list == ["ALPACA::asset-a", "ALPACA::asset-b"]
    # ms-markets 5.0.0 requires the table; the run writes its bars profile's (ADR 0029).
    assert (
        interpolated_prices.call_args.kwargs["output_table"]
        is INTERPOLATED_PRICES_BY_PROFILE[("1d", "iex", "raw")]
    )
