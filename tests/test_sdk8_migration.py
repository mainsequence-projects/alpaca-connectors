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
    build_account_interpolated_prices,
    resolve_alpaca_bars_time_index_meta_table_uid,
)


def test_runtime_dependency_versions_match_the_sdk9_metatables_stack() -> None:
    assert version("mainsequence") in SpecifierSet(">=9.0.38,<10")
    assert version("mainsequence-metatable") in SpecifierSet(">=0.1.79,<0.2")
    assert version("ms-markets") in SpecifierSet(">=6.2.1,<7")


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
        "alpaca_connectors__portfolioeventledgerts__main",
        "alpaca_connectors__ordersts__main",
        "alpaca_connectors__ordereventsts__main",
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
        # An order's states, events and trades name its Order row (ms-markets ADR 0064).
        "ms_markets__order",
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


def test_interpolated_prices_take_their_assets_from_the_accounts_bars() -> None:
    import uuid

    from alpaca_connectors.market_data.account_bars import AccountStockBars

    node = object()
    account_uid = uuid.UUID("66666666-6666-4666-8666-666666666666")

    with patch(
        "msm_portfolios.contrib.prices.updaters.InterpolatedPrices",
        return_value=node,
    ) as interpolated_prices:
        result = build_account_interpolated_prices(
            account_uid=account_uid, profile=("1d", "IEX", "raw")
        )

    assert result is node
    # The source is the account's bars updater on the profile, a node of the run (ADR 0033).
    interpolation_config = interpolated_prices.call_args.kwargs["interpolation_config"]
    source = interpolation_config.source_price_instance
    assert isinstance(source, AccountStockBars)
    assert source.config.account_uid == account_uid
    assert source.bars_profile == ("1d", "iex", "raw")
    # No asset in the configuration: the assets are the source's (ms-markets 6.2.1). Each asset's
    # bars follow the stored calendar registration linked it to (ADR 0026).
    assert interpolation_config.asset_list is None
    # ms-markets 5.0.0 requires the table; the run writes its bars profile's (ADR 0029).
    assert (
        interpolated_prices.call_args.kwargs["output_table"]
        is INTERPOLATED_PRICES_BY_PROFILE[("1d", "iex", "raw")]
    )
