"""Storage-first contract tests for the Alpaca bars MetaTable classes.

These assert the schema the migration provider will register and the DataNode will bind to,
without needing a platform backend.
"""

from __future__ import annotations

import pytest
from msm.settings import ASSET_IDENTIFIER_DIMENSION

from src.markets_storage.alpaca_bars import (
    AlpacaStockBars1dIexRawStorage,
    AlpacaStockBars1dSipAllStorage,
    project_storage_models,
    storage_for,
)

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume", "trade_count", "vwap")


def test_metatable_identifier_preserves_legacy_node_identifier():
    # These stable identifiers remain part of the public DataNode configuration contract.
    assert AlpacaStockBars1dSipAllStorage.__metatable_identifier__ == "alpaca_stock_bars_1d_sip_all"
    assert AlpacaStockBars1dIexRawStorage.__metatable_identifier__ == "alpaca_stock_bars_1d_iex_raw"


def test_index_names_are_time_then_asset_identifier():
    for storage in (AlpacaStockBars1dSipAllStorage, AlpacaStockBars1dIexRawStorage):
        assert storage.__index_names__ == ["time_index", ASSET_IDENTIFIER_DIMENSION]
        assert storage.__time_index_name__ == "time_index"
        assert storage.__cadence__ == "1d"


def test_physical_table_names_include_bar_configuration():
    assert AlpacaStockBars1dSipAllStorage.__table__.name.endswith("bars_1d_sip_all")
    assert AlpacaStockBars1dIexRawStorage.__table__.name.endswith("bars_1d_iex_raw")


def test_asset_identifier_is_first_non_time_identity_dimension():
    # get_asset_update_range_map_great_or_equal() requires asset_identifier at index 0 of the
    # identity dimensions (everything after time_index).
    identity_dimensions = AlpacaStockBars1dSipAllStorage.__index_names__[1:]
    assert identity_dimensions.index(ASSET_IDENTIFIER_DIMENSION) == 0


def test_storage_has_all_ohlcv_value_columns_as_float():
    table = AlpacaStockBars1dSipAllStorage.__table__
    for column_name in OHLCV_COLUMNS:
        assert column_name in table.columns, f"missing {column_name}"
        assert "FLOAT" in str(table.columns[column_name].type).upper()


def test_asset_identifier_foreign_key_targets_asset_table():
    table = AlpacaStockBars1dSipAllStorage.__table__
    fk_targets = {fk.target_fullname for fk in table.foreign_keys}
    assert any(target.endswith("asset.unique_identifier") for target in fk_targets), fk_targets


def test_registry_resolves_production_triple_and_rejects_unknown():
    assert storage_for("1d", "sip", "all") is AlpacaStockBars1dSipAllStorage
    assert storage_for("1d", "iex", "raw") is AlpacaStockBars1dIexRawStorage
    with pytest.raises(ValueError):
        storage_for("1m", "iex", "raw")


def test_project_storage_models_lists_registered_classes():
    assert AlpacaStockBars1dSipAllStorage in project_storage_models()
    assert AlpacaStockBars1dIexRawStorage in project_storage_models()


def test_storage_identity_components_include_feed_adjustment():
    assert AlpacaStockBars1dSipAllStorage.__metatable_extra_hash_components__ == {
        "feed": "sip",
        "adjustment": "all",
    }
    assert AlpacaStockBars1dIexRawStorage.__metatable_extra_hash_components__ == {
        "feed": "iex",
        "adjustment": "raw",
    }
