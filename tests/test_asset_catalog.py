from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from alpaca_connectors.assets.catalog import list_assets


def test_asset_list_pages_own_details_and_hydrates_markets_fields_in_batches() -> None:
    category_uid = uuid.UUID("22222222-2222-4222-8222-222222222222")
    asset_uid = uuid.UUID("33333333-3333-4333-8333-333333333333")
    alpaca_asset_id = "11111111-1111-4111-8111-111111111111"
    own_row = {
        "asset_uid": asset_uid,
        "alpaca_asset_id": alpaca_asset_id,
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "exchange": "NASDAQ",
        "status": "active",
        "tradable": True,
    }
    markets_calls: list[tuple[str, object]] = []
    with (
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
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
            side_effect=[[own_row], [{"count": 1}]],
        ),
        patch(
            "alpaca_connectors.markets_api.category_member_asset_uids",
            lambda uid: markets_calls.append(("members", uid)) or [str(asset_uid)],
        ),
        patch(
            "alpaca_connectors.markets_api.assets_by_uid",
            lambda uids: (
                markets_calls.append(("assets", list(uids)))
                or {
                    str(asset_uid): SimpleNamespace(
                        uid=asset_uid,
                        unique_identifier=f"ALPACA::{alpaca_asset_id}",
                        asset_type="equity",
                    )
                }
            ),
        ),
        patch(
            "alpaca_connectors.markets_api.openfigi_details_by_asset_uid",
            lambda uids: markets_calls.append(("figi", list(uids))) or {},
        ),
    ):
        items, total = list_assets(
            category_uid=category_uid,
            limit=25,
            offset=0,
            ordering="ticker",
        )

    assert items == [
        {
            "uid": asset_uid,
            "unique_identifier": f"ALPACA::{alpaca_asset_id}",
            "asset_type": "equity",
            "alpaca_asset_id": alpaca_asset_id,
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "exchange": "NASDAQ",
            "status": "active",
            "tradable": True,
            "figi": None,
            "composite_figi": None,
        }
    ]
    assert total == 1
    assert compile_statement.call_count == 2
    assert execute_operation.call_count == 2
    assert markets_calls == [
        ("members", category_uid),
        ("assets", [str(asset_uid)]),
        ("figi", [str(asset_uid)]),
    ]
    page_statement = compile_statement.call_args_list[0].args[0]
    sql = str(page_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "ms_markets__" not in sql
    assert asset_uid.hex in sql
