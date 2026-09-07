from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from api.app.schemas import BarConfigurationCreateRequest, BarConfigurationUpdateRequest
from api.app.services.bar_configurations import (
    create_configuration as create_api_configuration,
)
from api.app.services.bar_configurations import (
    get_configuration as get_api_configuration,
)
from api.app.services.bar_configurations import (
    update_configuration as update_api_configuration,
)
from pydantic import ValidationError

from src.holdings import held_asset_identifiers_from_snapshot_rows
from src.holdings.services import _recent_holdings_set_statement
from src.market_data.alpaca_bars import AlpacaStockBarsConfig
from src.market_data.configurations import (
    AlpacaBarsConfiguration,
    AlpacaBarsConfigurationAssetTable,
    AlpacaBarsConfigurationTable,
    _replace_memberships,
    project_configuration_models,
    validate_configuration_scope,
)
from src.market_data.services import (
    MarketDataDataset,
    _physical_dataset_update_statistics,
    build_market_data_update,
    execute_market_data_update,
)
from src.market_data.storage import AlpacaStockBars1dSipAllStorage


def _typed_configuration_row() -> AlpacaBarsConfiguration:
    return AlpacaBarsConfiguration(
        uid=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        name="Daily bars",
        description="Daily account holdings",
        enabled=True,
        account_uid=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        asset_source="account_holdings",
        asset_uids=[],
        universe_uid=None,
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        created_at=datetime(2026, 9, 5, tzinfo=UTC),
        updated_at=datetime(2026, 9, 5, tzinfo=UTC),
    )


def test_api_service_projects_typed_configuration_rows_for_create_get_and_update() -> None:
    row = _typed_configuration_row()
    create_request = BarConfigurationCreateRequest(
        name=row.name,
        description=row.description,
        enabled=row.enabled,
        account_uid=str(row.account_uid),
        asset_source=row.asset_source,
        asset_uids=[],
        universe_uid=None,
        frequency_id=row.frequency_id,
        feed=row.feed,
        adjustment=row.adjustment,
    )

    with (
        patch(
            "api.app.services.bar_configurations.create_bar_configuration",
            return_value=row,
        ),
        patch(
            "api.app.services.bar_configurations.get_bar_configuration",
            return_value=row,
        ),
        patch(
            "api.app.services.bar_configurations.update_bar_configuration",
            return_value=row,
        ),
    ):
        created = create_api_configuration(create_request)
        retrieved = get_api_configuration(str(row.uid))
        updated = update_api_configuration(
            str(row.uid),
            BarConfigurationUpdateRequest(name="Updated"),
        )

    assert created.uid == str(row.uid)
    assert retrieved is not None and retrieved.account_uid == str(row.account_uid)
    assert updated.asset_uids == []


def test_configuration_table_has_uuid_identity_and_no_dataset_pointer() -> None:
    table = AlpacaBarsConfigurationTable.__table__
    assert [column.name for column in table.primary_key.columns] == ["uid"]
    assert "unique_identifier" not in table.columns
    assert "dataset_uid" not in table.columns
    assert "account_uid" in table.columns
    assert "asset_source" in table.columns
    assert "universe_uid" in table.columns
    universe_foreign_key = next(
        foreign_key
        for foreign_key in table.foreign_keys
        if foreign_key.parent.name == "universe_uid"
    )
    assert universe_foreign_key.target_fullname == "alpaca_connectors__asset_universe.uid"


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


def test_explicit_asset_memberships_use_one_bulk_upsert_and_one_stale_delete() -> None:
    configuration_uid = uuid.UUID("11111111-1111-4111-8111-111111111111")
    asset_uids = [uuid.UUID(int=index + 1) for index in range(503)]
    context = object()
    returned_rows = [
        {"configuration_uid": str(configuration_uid), "asset_uid": str(asset_uid)}
        for asset_uid in asset_uids
    ]

    with (
        patch(
            "msm.bootstrap.resolve_runtime",
            return_value=SimpleNamespace(context=context),
        ),
        patch(
            "msm.repositories.crud.bulk_upsert_model",
            return_value={"rows": returned_rows},
        ) as bulk_upsert,
        patch(
            "msm.repositories.base.compile_markets_statement",
            return_value="delete-stale-operation",
        ) as compile_statement,
        patch("msm.repositories.base.execute_markets_operation") as execute_operation,
    ):
        _replace_memberships(configuration_uid, asset_uids)

    bulk_upsert.assert_called_once_with(
        context,
        model=AlpacaBarsConfigurationAssetTable,
        values=[
            {"configuration_uid": configuration_uid, "asset_uid": asset_uid}
            for asset_uid in asset_uids
        ],
        conflict_columns=("configuration_uid", "asset_uid"),
    )
    assert len(bulk_upsert.call_args.kwargs["values"]) == 503
    compile_statement.assert_called_once()
    execute_operation.assert_called_once_with("delete-stale-operation", context=context)


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


def test_physical_dataset_statistics_are_grouped_by_asset() -> None:
    storage = AlpacaStockBars1dSipAllStorage
    rows = [
        {
            "asset_identifier": "ALPACA::A",
            "earliest_observation": datetime(2020, 1, 2, tzinfo=UTC),
            "latest_observation": datetime(2024, 1, 2, tzinfo=UTC),
        },
        {
            "asset_identifier": "ALPACA::B",
            "earliest_observation": datetime(2021, 1, 2, tzinfo=UTC),
            "latest_observation": datetime(2023, 1, 2, tzinfo=UTC),
        },
    ]
    runtime = SimpleNamespace(context=object())

    with (
        patch("src.runtime.start_markets_engine", return_value=runtime),
        patch("msm.repositories.base.compile_markets_statement", return_value=object()),
        patch("msm.repositories.base.execute_markets_operation", return_value=rows),
    ):
        statistics = _physical_dataset_update_statistics(storage)

    assert statistics.index_min == {
        "ALPACA::A": datetime(2020, 1, 2, tzinfo=UTC),
        "ALPACA::B": datetime(2021, 1, 2, tzinfo=UTC),
    }
    assert statistics.index_progress == {
        "ALPACA::A": datetime(2024, 1, 2, tzinfo=UTC),
        "ALPACA::B": datetime(2023, 1, 2, tzinfo=UTC),
    }
    assert statistics.global_index_progress == {
        "min": datetime(2020, 1, 2, tzinfo=UTC),
        "max": datetime(2024, 1, 2, tzinfo=UTC),
    }


def test_execute_uses_physical_bar_progress_instead_of_cached_updater_statistics() -> None:
    configuration_uid = uuid.uuid4()
    account_uid = uuid.uuid4()
    physical_statistics = object()
    scoped_physical_statistics = object()
    storage = object()
    node = MagicMock()
    node.scope_update_statistics_to_assets.return_value = scoped_physical_statistics
    node.run.return_value = (False, [object(), object()])
    summary = {
        "account_uid": str(account_uid),
        "dataset": {"row_count": 0},
        "configuration": {
            "frequency_id": "1d",
            "feed": "sip",
            "adjustment": "all",
        },
    }

    with (
        patch(
            "src.market_data.services.build_market_data_update",
            return_value=(node, summary),
        ),
        patch(
            "src.account.services.get_account_registration",
            return_value={
                "account_uid": str(account_uid),
                "api_key_secret_name": "alpaca-api-key",
                "secret_key_secret_name": "alpaca-secret-key",
                "is_paper": True,
            },
        ),
        patch("src.market_data.services.resolve_alpaca_credentials", return_value=object()),
        patch("src.market_data.services.build_alpaca_historical_data_client"),
        patch("src.market_data.services.build_alpaca_trading_client"),
        patch("src.market_data.storage.storage_for", return_value=storage),
        patch(
            "src.market_data.services._physical_dataset_update_statistics",
            return_value=physical_statistics,
        ) as physical_progress,
    ):
        result = execute_market_data_update(configuration_uid=configuration_uid)

    physical_progress.assert_called_once_with(storage)
    node.scope_update_statistics_to_assets.assert_called_once_with(physical_statistics)
    node.run.assert_called_once_with(override_update_stats=scoped_physical_statistics)
    assert result["rows_persisted"] == 2
