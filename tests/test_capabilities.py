from __future__ import annotations

from alpaca_connectors.api.app.capabilities import (
    CAPABILITIES,
    capability_features,
    capability_summaries,
)


def test_capability_catalog_keys_are_unique_and_in_navigation_order() -> None:
    keys = [capability.key for capability in CAPABILITIES]

    assert keys == [
        "project_state",
        "assets",
        "universes",
        "market_data",
        "accounts",
        "holdings",
        "execution",
        "portfolios",
        "operations",
    ]
    assert len(keys) == len(set(keys))
    assert "connections" not in keys


def test_capability_summaries_are_json_safe() -> None:
    summaries = capability_summaries()

    assert [summary["key"] for summary in summaries] == [item.key for item in CAPABILITIES]
    assert all(isinstance(summary["contents"], tuple) for summary in summaries)


def test_capability_features_mirror_the_catalog() -> None:
    features = capability_features()

    assert [feature["id"] for feature in features] == [item.key for item in CAPABILITIES]
    assert {"id": "execution", "status": "available"} in features
    assert {"id": "portfolios", "status": "partial"} in features
