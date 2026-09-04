from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from src.assets.resolution import (
    assets_by_uids,
    ticker_and_optional_figi_by_unique_identifiers,
)


def test_asset_uid_resolution_executes_once_for_503_assets() -> None:
    asset_uids = [uuid.UUID(int=index + 1) for index in range(503)]
    rows = [
        {
            "uid": asset_uid,
            "unique_identifier": f"ALPACA::{asset_uid}",
            "asset_type": "equity",
        }
        for asset_uid in asset_uids
    ]
    context = object()

    with (
        patch(
            "src.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=context),
        ),
        patch(
            "msm.repositories.base.compile_markets_statement",
            return_value="asset-set-query",
        ) as compile_statement,
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={"rows": rows},
        ) as execute_operation,
    ):
        result = assets_by_uids(asset_uids)

    assert len(result) == 503
    compile_statement.assert_called_once()
    execute_operation.assert_called_once_with("asset-set-query", context=context)


def test_bar_identity_details_execute_once_for_503_assets() -> None:
    identifiers = [f"ALPACA::{uuid.UUID(int=index + 1)}" for index in range(503)]
    rows = [
        {
            "unique_identifier": identifier,
            "ticker": f"TICKER{index}",
            "figi": None,
        }
        for index, identifier in enumerate(identifiers)
    ]
    context = object()

    with (
        patch(
            "src.runtime.start_markets_engine",
            return_value=SimpleNamespace(context=context),
        ),
        patch(
            "msm.repositories.base.compile_markets_statement",
            return_value="asset-details-set-query",
        ) as compile_statement,
        patch(
            "msm.repositories.base.execute_markets_operation",
            return_value={"rows": rows},
        ) as execute_operation,
    ):
        result = ticker_and_optional_figi_by_unique_identifiers(identifiers)

    assert len(result) == 503
    compile_statement.assert_called_once()
    execute_operation.assert_called_once_with("asset-details-set-query", context=context)
