from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pandas as pd
import pytest
from msm.models.assets.categories import (
    AssetCategoryMembershipTable,
    AssetCategoryTable,
)

from src.assets import RegisteredAlpacaAssetReference
from src.assets.alpaca_asset_details import build_alpaca_unique_identifier
from src.universes import AssetUniverseTable
from src.universes.materialized import (
    create_asset_universe_configuration,
    get_asset_universe_view,
    list_asset_universes,
    update_asset_universe,
)
from src.universes.services import (
    AssetUniverseRunResult,
    materialize_asset_universe,
    preview_asset_universe,
    run_asset_universe,
)
from src.universes.sources import (
    UniverseSourceTable,
    delete_universe_source,
    update_universe_source,
)


def test_asset_universe_has_real_source_and_category_foreign_keys() -> None:
    table = AssetUniverseTable.__table__

    assert [column.name for column in table.primary_key] == ["uid"]
    assert {column.name for column in table.columns} == {
        "uid",
        "source_uid",
        "asset_category_uid",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert {foreign_key.target_fullname for foreign_key in table.foreign_keys} == {
        "alpaca_connectors__universe_source.uid",
        "ms_markets__assetcategory.uid",
    }
    assert all(foreign_key.ondelete == "RESTRICT" for foreign_key in table.foreign_keys)
    unique_indexes = {
        tuple(column.name for column in index.columns) for index in table.indexes if index.unique
    }
    assert unique_indexes == {("source_uid",), ("asset_category_uid",)}


def test_creation_persists_distinct_universe_source_and_category_uids_without_metadata() -> None:
    source = SimpleNamespace(
        uid=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        symbol="IVV",
        source_url="https://example.com/ivv",
        enabled=True,
    )
    category = SimpleNamespace(uid=uuid.UUID("22222222-2222-4222-8222-222222222222"))
    registered_universe = SimpleNamespace(uid=uuid.UUID("33333333-3333-4333-8333-333333333333"))
    created = {
        "uid": str(registered_universe.uid),
        "source_uid": str(source.uid),
        "asset_category_uid": str(category.uid),
        "asset_count": 0,
        "asset_category": {
            "uid": str(category.uid),
            "unique_identifier": "HOLDINGS__IVV",
            "display_name": "iShares Core S&P 500 ETF",
            "description": "Configured holdings universe for ETF IVV.",
        },
    }
    with (
        patch("src.runtime.start_markets_engine"),
        patch("msm.api.assets.AssetCategory.get_by_unique_identifier", return_value=None),
        patch("src.universes.sources.list_universe_sources", return_value=([source], 1)),
        patch(
            "src.universes.materialized.get_asset_universe_by_source_uid",
            return_value=None,
        ),
        patch(
            "msm.api.assets.AssetCategory.create",
            return_value=category,
        ) as create_category,
        patch(
            "src.universes.materialized.create_asset_universe",
            return_value=registered_universe,
        ) as create_universe,
        patch(
            "src.universes.materialized.get_asset_universe_view",
            return_value=created,
        ),
    ):
        result = create_asset_universe_configuration(
            name="iShares Core S&P 500 ETF",
            symbol="ivv",
            source_url="https://example.com/ivv",
        )

    assert result == created
    assert result["uid"] != result["asset_category_uid"]
    create_category.assert_called_once_with(
        unique_identifier="HOLDINGS__IVV",
        display_name="iShares Core S&P 500 ETF",
        description="Configured holdings universe for ETF IVV.",
        metadata_json=None,
    )
    create_universe.assert_called_once_with(
        source_uid=source.uid,
        asset_category_uid=category.uid,
        is_active=True,
    )


def test_list_query_scopes_governed_operations_with_metatable_models() -> None:
    page_row = {
        "uid": "universe-uid",
        "source_uid": "source-uid",
        "asset_category_uid": "category-uid",
        "is_active": True,
        "created_at": None,
        "updated_at": None,
        "symbol": "IVV",
        "source_url": "https://example.com/ivv",
        "display_name": "IVV holdings",
        "description": None,
        "asset_count": 1,
    }
    with (
        patch("src.runtime.start_markets_engine"),
        patch(
            "msm.bootstrap.resolve_runtime",
            return_value=SimpleNamespace(context=object()),
        ),
        patch(
            "msm.repositories.base.compile_markets_statement",
            side_effect=["page-operation", "count-operation"],
        ) as compile_statement,
        patch("msm.repositories.base.execute_markets_operation", return_value={}),
        patch(
            "msm.api.base.operation_result_rows",
            side_effect=[[page_row], [{"count": 1}]],
        ),
    ):
        items, total = list_asset_universes()

    assert items == [page_row]
    assert total == 1
    expected_models = [
        UniverseSourceTable,
        AssetCategoryTable,
        AssetCategoryMembershipTable,
        AssetUniverseTable,
    ]
    assert compile_statement.call_count == 2
    assert all(
        call.kwargs["models"] == expected_models for call in compile_statement.call_args_list
    )


def test_universe_detail_returns_linked_category_without_loading_member_assets() -> None:
    universe_uid = uuid.UUID("33333333-3333-4333-8333-333333333333")
    source_uid = uuid.UUID("11111111-1111-4111-8111-111111111111")
    category_uid = uuid.UUID("22222222-2222-4222-8222-222222222222")
    row = {
        "uid": universe_uid,
        "source_uid": source_uid,
        "asset_category_uid": category_uid,
        "is_active": True,
        "created_at": None,
        "updated_at": None,
        "symbol": "IVV",
        "source_url": "https://example.com/ivv",
        "category_unique_identifier": "HOLDINGS__IVV",
        "display_name": "IVV holdings",
        "description": "Configured holdings universe for ETF IVV.",
        "asset_count": 503,
    }
    with (
        patch(
            "src.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=object()),
        ),
        patch(
            "msm.repositories.base.compile_markets_statement",
            return_value="detail-operation",
        ) as compile_statement,
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={},
        ) as execute_operation,
        patch("msm.api.base.operation_result_rows", return_value=[row]),
    ):
        result = get_asset_universe_view(universe_uid)

    assert result == {
        "uid": str(universe_uid),
        "source_uid": str(source_uid),
        "asset_category_uid": str(category_uid),
        "display_name": "IVV holdings",
        "symbol": "IVV",
        "source_url": "https://example.com/ivv",
        "description": "Configured holdings universe for ETF IVV.",
        "is_active": True,
        "asset_count": 503,
        "asset_category": {
            "uid": str(category_uid),
            "unique_identifier": "HOLDINGS__IVV",
            "display_name": "IVV holdings",
            "description": "Configured holdings universe for ETF IVV.",
        },
        "created_at": None,
        "updated_at": None,
    }
    assert "asset_uids" not in result
    assert "asset_identifiers" not in result
    compile_statement.assert_called_once()
    execute_operation.assert_called_once()


def test_update_stores_lifecycle_on_asset_universe_not_category_metadata() -> None:
    existing = {
        "uid": "universe-uid",
        "asset_category_uid": "category-uid",
        "is_active": True,
    }
    updated = {**existing, "is_active": False}

    with (
        patch(
            "src.universes.materialized.get_asset_universe_view",
            side_effect=[existing, updated],
        ),
        patch("msm.api.assets.AssetCategory.update") as update_category,
        patch("src.universes.materialized.update_asset_universe_state") as update_state,
    ):
        result = update_asset_universe("universe-uid", is_active=False)

    assert result == updated
    update_category.assert_not_called()
    update_state.assert_called_once_with("universe-uid", is_active=False)


def test_linked_source_cannot_be_deleted() -> None:
    source = SimpleNamespace(uid="source-uid")
    universe = SimpleNamespace(uid="universe-uid")
    with (
        patch("src.universes.sources.get_universe_source", return_value=source),
        patch(
            "src.universes.registry.get_asset_universe_by_source_uid",
            return_value=universe,
        ),
        patch("src.universes.sources.UniverseSource.delete") as delete,
    ):
        with pytest.raises(ValueError, match="linked to Asset Universe universe-uid"):
            delete_universe_source("source-uid")

    delete.assert_not_called()


def test_linked_source_symbol_cannot_change() -> None:
    source = SimpleNamespace(
        uid="source-uid",
        name="IVV source",
        symbol="IVV",
        source_url="https://example.com/ivv",
        enabled=True,
    )
    universe = SimpleNamespace(uid="universe-uid")
    with (
        patch("src.universes.sources.get_universe_source", return_value=source),
        patch(
            "src.universes.registry.get_asset_universe_by_source_uid",
            return_value=universe,
        ),
        patch("src.universes.sources.UniverseSource.update") as update,
    ):
        with pytest.raises(ValueError, match="recreate the universe"):
            update_universe_source("source-uid", symbol="SPY")

    update.assert_not_called()


def test_universe_preview_uses_the_selected_run_account_for_constituent_registration() -> None:
    universe = SimpleNamespace(
        uid="universe-uid",
        source_uid="source-uid",
        asset_category_uid="category-uid",
        is_active=True,
    )
    source = SimpleNamespace(
        uid="source-uid",
        enabled=True,
        symbol="IVV",
        source_url="https://example.com/ivv",
    )
    holdings_plan = SimpleNamespace(
        component_symbols=["AAPL", "MSFT"],
        fund_holdings=object(),
    )
    registration_plan = SimpleNamespace(missing_symbols_from_alpaca=[])
    registration_resolution = SimpleNamespace(
        missing_assets=[],
        unresolved_symbols_from_alpaca=[],
        existing_off_catalog_assets_by_symbol={},
    )

    with (
        patch(
            "src.universes.services.require_asset_universe_links",
            return_value=(universe, source, SimpleNamespace(uid="category-uid")),
        ),
        patch(
            "src.universes.services.build_holdings_asset_category_plan",
            return_value=holdings_plan,
        ) as extract,
        patch(
            "etfhextractor.derive_component_weights_from_holdings",
            return_value={"AAPL": 60.0, "MSFT": 40.0},
        ),
        patch(
            "src.universes.services.build_alpaca_us_equity_registration_plan",
            return_value=registration_plan,
        ) as build_registration,
        patch(
            "src.universes.services.resolve_alpaca_us_equity_registration_plan",
            return_value=registration_resolution,
        ) as resolve_registration,
    ):
        plan = preview_asset_universe(
            "universe-uid",
            account_uid="account-uid",
            timeout=45.0,
        )

    assert plan.account_uid == "account-uid"
    assert plan.timeout == 45.0
    assert plan.component_weights_by_symbol == {"AAPL": 60.0, "MSFT": 40.0}
    assert plan.holdings_plan is holdings_plan
    extract.assert_called_once()
    assert extract.call_args.kwargs["etf_ticker"] == "IVV"
    assert callable(extract.call_args.kwargs["resolve_existing_assets_by_ticker_fn"])
    build_registration.assert_called_once_with(
        account_uid="account-uid",
        symbols=["AAPL", "MSFT"],
        timeout=45.0,
        enrich_openfigi=False,
    )
    resolve_registration.assert_called_once_with(registration_plan, timeout=45.0)


def test_materialization_writes_only_the_category_linked_by_the_selected_universe() -> None:
    universe = SimpleNamespace(
        uid="universe-uid",
        source_uid="source-uid",
        asset_category_uid="22222222-2222-4222-8222-222222222222",
        is_active=True,
    )
    source = SimpleNamespace(uid="source-uid", enabled=True, symbol="IVV", source_url="https://x")
    category = SimpleNamespace(
        uid=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        unique_identifier="HOLDINGS__IVV",
        display_name="IVV holdings",
    )
    asset_uid = uuid.UUID("44444444-4444-4444-8444-444444444444")
    alpaca_asset_id = uuid.UUID("55555555-5555-4555-8555-555555555555")
    holdings_plan = SimpleNamespace(component_symbols=["AAPL"])
    registration_plan = SimpleNamespace(
        requested_symbol_aliases={},
        missing_symbols_from_alpaca=[],
        alpaca_assets=[SimpleNamespace(symbol="AAPL", alpaca_asset_id=alpaca_asset_id)],
    )
    registration_resolution = SimpleNamespace(existing_off_catalog_assets_by_symbol={})
    plan = SimpleNamespace(
        universe_uid="universe-uid",
        account_uid="account-uid",
        timeout=30.0,
        observed_at=dt.datetime(2026, 9, 4, 8, 0, tzinfo=dt.UTC),
        holdings_plan=holdings_plan,
        component_weights_by_symbol={"AAPL": 100.0},
        registration_plan=registration_plan,
        registration_resolution=registration_resolution,
        has_blockers=lambda: False,
    )
    with (
        patch(
            "src.universes.services.require_asset_universe_links",
            return_value=(universe, source, category),
        ),
        patch(
            "src.universes.services.register_alpaca_us_equity_assets",
            return_value={
                "assets": {"AAPL": str(asset_uid)},
                "existing_assets": {},
                "created_assets": {"AAPL": str(asset_uid)},
                "openfigi_unmatched_symbols": [],
            },
        ) as register,
        patch(
            "msm.api.assets.AssetCategory.replace_memberships",
            return_value=[],
        ) as replace,
    ):
        result = materialize_asset_universe(
            "universe-uid",
            account_uid="account-uid",
            plan=plan,
        )

    replace.assert_called_once_with(category_uid=category.uid, asset_uids=[asset_uid])
    register.assert_called_once_with(
        registration_resolution=registration_resolution,
        timeout=30.0,
    )
    assert result.asset_uids == [asset_uid]
    assert result.asset_identifiers_by_symbol == {
        "AAPL": f"ALPACA::{alpaca_asset_id}"
    }
    assert result.component_weights_by_symbol == {"AAPL": 100.0}
    assert result.created_asset_uids_by_symbol == {"AAPL": str(asset_uid)}


def test_materialization_keeps_an_exact_registered_off_catalog_constituent() -> None:
    universe = SimpleNamespace(
        uid="universe-uid",
        source_uid="source-uid",
        asset_category_uid="22222222-2222-4222-8222-222222222222",
        is_active=True,
    )
    category = SimpleNamespace(
        uid=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        unique_identifier="HOLDINGS__IVV",
        display_name="IVV holdings",
    )
    asset_uid = uuid.UUID("44444444-4444-4444-8444-444444444444")
    alpaca_asset_id = uuid.UUID("c31e63a5-b5a2-417e-9ac3-278b4ff35cb5")
    reference = RegisteredAlpacaAssetReference(
        asset_uid=str(asset_uid),
        unique_identifier=build_alpaca_unique_identifier(alpaca_asset_id),
        alpaca_asset_id=alpaca_asset_id,
        symbol="HOLX",
    )
    registration_resolution = SimpleNamespace(
        existing_off_catalog_assets_by_symbol={"HOLX": reference}
    )
    plan = SimpleNamespace(
        universe_uid="universe-uid",
        account_uid="account-uid",
        timeout=30.0,
        observed_at=dt.datetime(2026, 9, 4, 8, 0, tzinfo=dt.UTC),
        holdings_plan=SimpleNamespace(component_symbols=["HOLX"]),
        component_weights_by_symbol={"HOLX": 100.0},
        registration_plan=SimpleNamespace(requested_symbol_aliases={}, alpaca_assets=[]),
        registration_resolution=registration_resolution,
        has_blockers=lambda: False,
    )
    with (
        patch(
            "src.universes.services.require_asset_universe_links",
            return_value=(universe, SimpleNamespace(), category),
        ),
        patch(
            "src.universes.services.register_alpaca_us_equity_assets",
            return_value={
                "assets": {"HOLX": str(asset_uid)},
                "existing_assets": {"HOLX": str(asset_uid)},
                "created_assets": {},
                "openfigi_unmatched_symbols": [],
            },
        ),
        patch("msm.api.assets.AssetCategory.replace_memberships", return_value=[]) as replace,
    ):
        result = materialize_asset_universe(
            "universe-uid",
            account_uid="account-uid",
            plan=plan,
        )

    replace.assert_called_once_with(category_uid=category.uid, asset_uids=[asset_uid])
    assert result.asset_uids == [asset_uid]
    assert result.asset_identifiers_by_symbol == {
        "HOLX": build_alpaca_unique_identifier(alpaca_asset_id)
    }


def test_run_publishes_the_prepared_universe_through_the_signal() -> None:
    observed_at = dt.datetime(2026, 9, 4, 8, 0, tzinfo=dt.UTC)
    plan = SimpleNamespace(universe_uid="universe-uid", account_uid="account-uid")
    materialization = AssetUniverseRunResult(
        unique_identifier="HOLDINGS__IVV",
        display_name="IVV holdings",
        asset_uids=[uuid.UUID("44444444-4444-4444-8444-444444444444")],
        account_uid="account-uid",
        observed_at=observed_at,
        component_weights_by_symbol={"AAPL": 100.0},
        asset_identifiers_by_symbol={"AAPL": "ALPACA::asset-uuid"},
        existing_asset_uids_by_symbol={},
        created_asset_uids_by_symbol={"AAPL": "asset-uid"},
        openfigi_unmatched_symbols=[],
    )
    frame = pd.DataFrame({"signal_weight": [1.0]})
    signal = Mock(
        signal_uid="signal-uid",
        last_universe_materialization=materialization,
    )
    signal.run.return_value = (False, frame)

    with (
        patch("src.universes.services.preview_asset_universe", return_value=plan) as preview,
        patch(
            "src.portfolios.alpaca_etf_signal.build_alpaca_etf_holdings_signal",
            return_value=signal,
        ) as build_signal,
    ):
        result = run_asset_universe(
            "universe-uid",
            account_uid="account-uid",
            timeout=45.0,
        )

    preview.assert_called_once_with(
        "universe-uid",
        account_uid="account-uid",
        timeout=45.0,
    )
    build_signal.assert_called_once_with(
        universe_uid="universe-uid",
        account_uid="account-uid",
        prepared_plan=plan,
    )
    signal.run.assert_called_once_with()
    assert result.signal_uid == "signal-uid"
    assert result.signal_weight_row_count == 1


def test_membership_replacement_bulk_upserts_then_deletes_stale_rows_once() -> None:
    from msm.repositories.asset_categories import replace_asset_category_memberships

    category_uid = uuid.UUID("22222222-2222-4222-8222-222222222222")
    asset_uids = [uuid.UUID(int=index + 1) for index in range(503)]
    context = object()

    with (
        patch(
            "msm.repositories.asset_categories.build_bulk_upsert_model_operation",
            return_value="bulk-upsert-operation",
        ) as build_bulk_upsert,
        patch(
            "msm.repositories.asset_categories."
            "build_delete_stale_asset_category_memberships_operation",
            return_value="delete-stale-operation",
        ) as build_delete_stale,
        patch(
            "msm.repositories.asset_categories.execute_markets_operation",
            side_effect=[{"rows": []}, {"rows": []}],
        ) as execute_operation,
    ):
        result = replace_asset_category_memberships(
            context,
            category_uid=category_uid,
            asset_uids=asset_uids,
        )

    assert result == [{"rows": []}, {"rows": []}]
    build_bulk_upsert.assert_called_once_with(
        context,
        model=AssetCategoryMembershipTable,
        values=[{"category_uid": category_uid, "asset_uid": asset_uid} for asset_uid in asset_uids],
        conflict_columns=("category_uid", "asset_uid"),
    )
    assert len(build_bulk_upsert.call_args.kwargs["values"]) == 503
    build_delete_stale.assert_called_once_with(
        context,
        category_uid=category_uid,
        retained_asset_uids=asset_uids,
    )
    assert execute_operation.call_args_list == [
        call("bulk-upsert-operation", context=context),
        call("delete-stale-operation", context=context),
    ]


def test_market_data_rejects_inactive_asset_universe() -> None:
    from src.market_data.services import _asset_identifiers_from_universe_uid

    universe = SimpleNamespace(is_active=False)
    category = SimpleNamespace(
        uid=uuid.UUID("22222222-2222-4222-8222-222222222222"),
        unique_identifier="HOLDINGS__IVV",
    )
    with patch(
        "src.universes.require_asset_universe_links",
        return_value=(universe, SimpleNamespace(), category),
    ):
        try:
            _asset_identifiers_from_universe_uid("universe-uid")
        except ValueError as exc:
            assert "inactive" in str(exc)
        else:
            raise AssertionError("Inactive Asset Universe was accepted as a market-data scope.")
