from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from msm.models import (
    AssetCategoryMembershipTable,
    AssetTable,
    OpenFigiAssetDetailsTable,
)

from src.assets.alpaca_asset_details import AlpacaAssetDetailsTable
from src.assets.catalog import list_assets


def test_asset_list_scopes_to_category_with_set_based_paginated_queries() -> None:
    category_uid = uuid.UUID("22222222-2222-4222-8222-222222222222")
    asset_row = {
        "uid": "asset-aapl",
        "unique_identifier": "ALPACA::11111111-1111-4111-8111-111111111111",
        "asset_type": "equity",
        "alpaca_asset_id": "11111111-1111-4111-8111-111111111111",
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "exchange": "NASDAQ",
        "status": "active",
        "tradable": True,
        "figi": None,
        "composite_figi": None,
    }
    with (
        patch(
            "src.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=object()),
        ),
        patch(
            "msm.repositories.base.compile_markets_statement",
            side_effect=["page-operation", "count-operation"],
        ) as compile_statement,
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={},
        ) as execute_operation,
        patch(
            "msm.api.base.operation_result_rows",
            side_effect=[[asset_row], [{"count": 1}]],
        ),
    ):
        items, total = list_assets(
            category_uid=category_uid,
            limit=25,
            offset=0,
            ordering="ticker",
        )

    assert items == [asset_row]
    assert total == 1
    assert compile_statement.call_count == 2
    assert execute_operation.call_count == 2
    expected_models = [
        AssetTable,
        AlpacaAssetDetailsTable,
        OpenFigiAssetDetailsTable,
        AssetCategoryMembershipTable,
    ]
    assert all(
        call.kwargs["models"] == expected_models for call in compile_statement.call_args_list
    )
    page_statement = compile_statement.call_args_list[0].args[0]
    sql = str(page_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "ms_markets__assetcategorymembership" in sql
    assert category_uid.hex in sql
