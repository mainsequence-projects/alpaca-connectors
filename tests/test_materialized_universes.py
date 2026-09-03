from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.universes.materialized import (
    create_materialized_universe_configuration,
    materialized_universe_is_active,
    materialized_universe_source_uid,
    update_materialized_universe,
)


def test_existing_universes_default_to_active() -> None:
    assert materialized_universe_is_active(None)
    assert materialized_universe_is_active({"provider": "ishares"})
    assert materialized_universe_is_active({"alpaca_connectors": {"active": "false"}})


def test_connector_metadata_controls_active_state() -> None:
    assert not materialized_universe_is_active({"alpaca_connectors": {"active": False}})
    assert materialized_universe_is_active({"alpaca_connectors": {"active": True}})


def test_connector_metadata_exposes_linked_source_uid() -> None:
    assert materialized_universe_source_uid(None) is None
    assert materialized_universe_source_uid({"alpaca_connectors": {}}) is None
    assert (
        materialized_universe_source_uid(
            {"alpaca_connectors": {"source_uid": "source-uid"}}
        )
        == "source-uid"
    )


def test_creation_persists_empty_universe_without_extracting_holdings() -> None:
    source = SimpleNamespace(
        uid="source-uid",
        symbol="IVV",
        source_url="https://example.com/ivv",
        enabled=True,
    )
    created = {
        "uid": "universe-uid",
        "source_uid": "source-uid",
        "asset_uids": [],
        "asset_identifiers": [],
        "asset_count": 0,
    }
    with (
        patch("src.runtime.start_markets_engine"),
        patch(
            "msm.api.assets.AssetCategory.get_by_unique_identifier",
            return_value=None,
        ),
        patch(
            "src.universes.sources.list_universe_sources",
            return_value=([source], 1),
        ),
        patch(
            "msm.api.assets.AssetCategory.create",
            return_value=SimpleNamespace(uid="universe-uid"),
        ) as create,
        patch(
            "src.universes.materialized.get_materialized_universe",
            return_value=created,
        ),
    ):
        result = create_materialized_universe_configuration(
            name="iShares Core S&P 500 ETF",
            symbol="ivv",
            source_url="https://example.com/ivv",
        )

    assert result == created
    create.assert_called_once_with(
        unique_identifier="HOLDINGS__IVV",
        display_name="iShares Core S&P 500 ETF",
        description="Configured holdings universe for ETF IVV.",
        metadata_json={
            "alpaca_connectors": {"active": True, "source_uid": "source-uid"}
        },
    )


def test_update_active_state_preserves_unrelated_metadata() -> None:
    existing = {
        "uid": "universe-uid",
        "metadata_json": {
            "provider": "ishares",
            "alpaca_connectors": {"active": True, "source_uid": "source-uid"},
        },
    }
    updated = {**existing, "is_active": False}

    with (
        patch(
            "src.universes.materialized.get_materialized_universe",
            side_effect=[existing, updated],
        ),
        patch("msm.api.assets.AssetCategory.update") as update,
    ):
        result = update_materialized_universe("universe-uid", is_active=False)

    assert result == updated
    update.assert_called_once_with(
        "universe-uid",
        {
            "metadata_json": {
                "provider": "ishares",
                "alpaca_connectors": {"active": False, "source_uid": "source-uid"},
            }
        },
    )


def test_market_data_rejects_inactive_universe() -> None:
    from src.market_data.services import _asset_identifiers_from_universe_uid

    category = SimpleNamespace(
        unique_identifier="HOLDINGS__IVV",
        metadata_json={"alpaca_connectors": {"active": False}},
    )
    with patch("msm.api.assets.AssetCategory.get_by_uid", return_value=category):
        try:
            _asset_identifiers_from_universe_uid("universe-uid")
        except ValueError as exc:
            assert "inactive" in str(exc)
        else:
            raise AssertionError("Inactive universe was accepted as a market-data scope.")
