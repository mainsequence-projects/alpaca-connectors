from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.assets.alpaca_asset_details import (
    AlpacaAssetDetails,
    AlpacaAssetDetailsTable,
    build_alpaca_unique_identifier,
    parse_alpaca_unique_identifier,
    upsert_alpaca_asset_details,
)

ASSET_UID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ALPACA_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _detail_payload() -> dict:
    return {
        "asset_uid": ASSET_UID,
        "alpaca_asset_id": ALPACA_ID,
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "asset_class": "us_equity",
        "exchange": "NASDAQ",
        "status": "active",
        "tradable": True,
        "marginable": True,
        "shortable": True,
        "easy_to_borrow": True,
        "fractionable": True,
        "attributes": [],
        "raw_payload": {},
        "refreshed_at": "2026-09-03T19:01:13Z",
    }


def test_alpaca_identifier_is_canonical_and_round_trips() -> None:
    identifier = build_alpaca_unique_identifier(str(ALPACA_ID).upper())
    assert identifier == f"ALPACA::{ALPACA_ID}"
    assert parse_alpaca_unique_identifier(identifier) == ALPACA_ID
    with pytest.raises(ValueError, match="Not an Alpaca asset identifier"):
        parse_alpaca_unique_identifier(f"FIGI::{ALPACA_ID}")


def test_alpaca_details_schema_is_one_to_one_with_unique_provider_id() -> None:
    table = AlpacaAssetDetailsTable.__table__
    assert [column.name for column in table.primary_key.columns] == ["asset_uid"]
    fk_targets = {foreign_key.target_fullname for foreign_key in table.foreign_keys}
    assert "ms_markets__asset.uid" in fk_targets
    assert any(
        index.unique and [column.name for column in index.columns] == ["alpaca_asset_id"]
        for index in table.indexes
    )
    assert not any(
        index.unique and [column.name for column in index.columns] == ["symbol"]
        for index in table.indexes
    )


def test_detail_row_uses_asset_uid_as_its_generic_row_uid() -> None:
    detail = AlpacaAssetDetails.model_validate(_detail_payload())

    assert detail.uid == ASSET_UID
    assert detail.asset_uid == ASSET_UID
    assert AlpacaAssetDetails.__upsert_keys__ == ("asset_uid",)
    assert [model.__name__ for model in AlpacaAssetDetails.__required_tables__] == [
        "AssetTable",
        "AlpacaAssetDetailsTable",
    ]


def test_detail_upsert_rejects_parent_identifier_mismatch() -> None:
    parent = SimpleNamespace(unique_identifier=f"ALPACA::{uuid.uuid4()}")
    with patch("msm.api.assets.Asset.get_by_uid", return_value=parent):
        with pytest.raises(ValueError, match="does not match its parent Asset"):
            upsert_alpaca_asset_details(
                asset_uid=ASSET_UID,
                values={"alpaca_asset_id": ALPACA_ID},
            )


def test_detail_upsert_is_idempotent_on_asset_uid() -> None:
    parent = SimpleNamespace(unique_identifier=build_alpaca_unique_identifier(ALPACA_ID))
    expected = object()
    values = {"alpaca_asset_id": ALPACA_ID, "symbol": "AAPL"}
    with (
        patch("msm.api.assets.Asset.get_by_uid", return_value=parent),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context="ctx")),
        patch("msm.repositories.crud.upsert_model", return_value="result") as upsert,
        patch(
            "src.assets.alpaca_asset_details.operation_result_rows",
            return_value=[{"asset_uid": ASSET_UID}],
        ),
        patch.object(AlpacaAssetDetails, "model_validate", return_value=expected),
    ):
        result = upsert_alpaca_asset_details(asset_uid=ASSET_UID, values=values)

    assert result is expected
    assert upsert.call_args.kwargs["conflict_columns"] == ("asset_uid",)
    assert upsert.call_args.kwargs["values"]["alpaca_asset_id"] == ALPACA_ID
