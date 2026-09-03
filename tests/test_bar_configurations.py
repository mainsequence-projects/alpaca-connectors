from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from api.app.schemas import BarConfigurationCreateRequest
from pydantic import ValidationError

from src.holdings import held_asset_identifiers_from_snapshot_rows
from src.holdings.services import _recent_holdings_set_statement
from src.market_data.alpaca_bars import AlpacaStockBarsConfig
from src.market_data.configurations import (
    AlpacaBarsConfigurationAssetTable,
    AlpacaBarsConfigurationTable,
    project_configuration_models,
    validate_configuration_scope,
)
from src.market_data.services import MarketDataDataset, build_market_data_update


def test_configuration_table_has_uuid_identity_and_no_dataset_pointer() -> None:
    table = AlpacaBarsConfigurationTable.__table__
    assert [column.name for column in table.primary_key.columns] == ["uid"]
    assert "unique_identifier" not in table.columns
    assert "dataset_uid" not in table.columns
    assert "account_uid" in table.columns
    assert "asset_source" in table.columns
    assert "universe_uid" in table.columns


def test_explicit_assets_use_a_normalized_composite_membership() -> None:
    table = AlpacaBarsConfigurationAssetTable.__table__
    assert [column.name for column in table.primary_key.columns] == [
        "configuration_uid",
        "asset_uid",
    ]
    targets = {foreign_key.target_fullname for foreign_key in table.foreign_keys}
    assert any(target.endswith("bars_configuration.uid") for target in targets)
    assert any(target.endswith("asset.uid") for target in targets)


def test_configuration_models_are_migration_managed() -> None:
    assert project_configuration_models() == [
        AlpacaBarsConfigurationTable,
        AlpacaBarsConfigurationAssetTable,
    ]


def test_scope_validation_owns_three_exclusive_source_shapes() -> None:
    asset_uid = uuid.uuid4()
    universe_uid = uuid.uuid4()
    assert validate_configuration_scope(
        asset_source="assets", asset_uids=[asset_uid, asset_uid], universe_uid=None
    ) == ("assets", [asset_uid], None)
    assert validate_configuration_scope(
        asset_source="universe", asset_uids=None, universe_uid=universe_uid
    ) == ("universe", [], universe_uid)
    assert validate_configuration_scope(
        asset_source="account_holdings", asset_uids=None, universe_uid=None
    ) == ("account_holdings", [], None)
    with pytest.raises(ValueError, match="forbids"):
        validate_configuration_scope(
            asset_source="account_holdings",
            asset_uids=[asset_uid],
            universe_uid=None,
        )


def test_runtime_config_hash_material_uses_source_identity_not_resolved_members() -> None:
    account_uid = uuid.uuid4()
    account_config = AlpacaStockBarsConfig(
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        asset_source="account_holdings",
        account_uid=account_uid,
    )
    assert account_config.asset_list is None
    assert account_config.account_uid == account_uid

    universe_config = AlpacaStockBarsConfig(
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        asset_source="universe",
        asset_category_unique_identifier="HOLDINGS__SPY",
    )
    assert universe_config.asset_list is None
    assert universe_config.asset_category_unique_identifier == "HOLDINGS__SPY"


def test_runtime_config_rejects_mixed_source_fields() -> None:
    with pytest.raises(ValidationError, match="forbids"):
        AlpacaStockBarsConfig(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            asset_source="account_holdings",
            account_uid=uuid.uuid4(),
            asset_list=["BBG000BBJQV0"],
        )


def test_account_holdings_asset_scope_excludes_cash_zeroes_and_duplicates() -> None:
    rows = [
        {"asset_identifier": "USD", "quantity": 100, "extra_details": {"kind": "cash"}},
        {"asset_identifier": "A", "quantity": 1, "extra_details": {}},
        {"asset_identifier": "A", "quantity": 2, "extra_details": {}},
        {"asset_identifier": "B", "quantity": 0, "extra_details": {}},
        {"asset_identifier": "C", "quantity": 1, "extra_details": {"kind": "cash"}},
    ]
    assert held_asset_identifiers_from_snapshot_rows(rows) == ["A"]


def test_account_holdings_window_is_inclusive_and_selects_the_newest_set() -> None:
    boundary = datetime(2026, 9, 3, 12, tzinfo=UTC)
    statement = _recent_holdings_set_statement(
        uuid.uuid4(),
        boundary=boundary,
        max_age=timedelta(days=30),
    )
    compiled = statement.compile()
    assert boundary in compiled.params.values()
    assert boundary - timedelta(days=30) in compiled.params.values()
    sql = str(compiled)
    assert "time_index >=" in sql
    assert "time_index <=" in sql
    assert "time_index DESC" in sql
    assert "LIMIT" in sql


def test_api_configuration_never_accepts_dataset_uid() -> None:
    with pytest.raises(ValidationError):
        BarConfigurationCreateRequest.model_validate(
            {
                "name": "Daily",
                "account_uid": str(uuid.uuid4()),
                "asset_source": "account_holdings",
                "frequency_id": "1d",
                "feed": "sip",
                "adjustment": "all",
                "dataset_uid": str(uuid.uuid4()),
            }
        )


def test_review_resolution_never_resolves_secret_values() -> None:
    account_uid = uuid.uuid4()
    configuration_uid = uuid.uuid4()
    asset_uid = uuid.uuid4()
    configuration = SimpleNamespace(
        uid=configuration_uid,
        enabled=True,
        account_uid=account_uid,
        asset_source="assets",
        asset_uids=[asset_uid],
        universe_uid=None,
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        model_dump=lambda mode=None: {
            "uid": str(configuration_uid),
            "account_uid": str(account_uid),
            "asset_source": "assets",
            "asset_uids": [str(asset_uid)],
            "universe_uid": None,
            "frequency_id": "1d",
            "feed": "sip",
            "adjustment": "all",
            "enabled": True,
            "name": "Daily",
            "description": None,
            "created_at": datetime(2026, 9, 3, tzinfo=UTC),
            "updated_at": datetime(2026, 9, 3, tzinfo=UTC),
        },
    )
    dataset = MarketDataDataset(
        uid=str(uuid.uuid4()),
        key="1d/sip/all",
        identifier="alpaca_stock_bars_1d_sip_all",
        physical_table="alpaca_connectors__bars_1d_sip_all",
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        cadence="1d",
        columns=["time_index", "asset_identifier", "close"],
        row_count=0,
        earliest_observation=None,
        latest_observation=None,
    )

    class FakeNode:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.update_hash = "resolved-update-hash"

    with (
        patch("src.runtime.account_runtime_models", return_value=[]),
        patch("src.runtime.start_markets_engine"),
        patch(
            "src.market_data.configurations.get_bar_configuration",
            return_value=configuration,
        ),
        patch(
            "src.account.services.get_account_registration",
            return_value={"account_uid": str(account_uid)},
        ),
        patch("src.market_data.services.storage_for", create=True),
        patch("src.market_data.storage.storage_for", return_value=object()),
        patch("src.market_data.services._dataset_from_storage", return_value=dataset),
        patch("src.market_data.services._asset_identifiers_from_uids", return_value=["A"]),
        patch("src.market_data.alpaca_bars.AlpacaStockBarsNode", FakeNode),
        patch("src.market_data.services.resolve_alpaca_credentials") as resolve_credentials,
    ):
        _, summary = build_market_data_update(configuration_uid=configuration_uid)

    resolve_credentials.assert_not_called()
    assert summary["dataset"]["uid"] == dataset.uid
    assert summary["asset_identifiers"] == ["A"]
