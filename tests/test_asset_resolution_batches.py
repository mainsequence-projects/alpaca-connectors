from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from alpaca_connectors.assets.resolution import (
    asset_unique_identifiers_for_category,
    assets_by_uids,
    assets_for_ticker,
    ticker_and_optional_figi_by_unique_identifiers,
)


def _asset(asset_uid: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        uid=asset_uid, unique_identifier=f"ALPACA::{asset_uid}", asset_type="equity"
    )


def test_asset_uid_resolution_reads_markets_in_batches_of_500() -> None:
    from msm.api.assets import Asset

    asset_uids = [uuid.UUID(int=index + 1) for index in range(503)]
    calls: list[dict] = []

    def fake_filter(cls=None, **kwargs):
        calls.append(kwargs)
        return [_asset(uuid.UUID(value)) for value in kwargs["uid__in"]]

    with patch.object(Asset, "filter", classmethod(lambda cls, **kw: fake_filter(**kw))):
        result = assets_by_uids(asset_uids)

    assert len(result) == 503
    assert [len(call["uid__in"]) for call in calls] == [500, 3]
    assert [call["limit"] for call in calls] == [500, 3]


def test_bar_identity_details_use_one_markets_read_and_one_own_query() -> None:
    from msm.api.assets import Asset, OpenFigiDetails

    asset_uids = [uuid.UUID(int=index + 1) for index in range(3)]
    identifiers = [f"ALPACA::{asset_uid}" for asset_uid in asset_uids]
    own_queries: list[object] = []

    def details_rows(where):
        own_queries.append(where)
        return [
            {"asset_uid": asset_uids[0], "symbol": "AAA"},
            {"asset_uid": asset_uids[1], "symbol": "BBB"},
        ]

    with (
        patch.object(
            Asset,
            "get_many_by_unique_identifier",
            classmethod(
                lambda cls, values: {value: _asset(uuid.UUID(value[8:])) for value in values}
            ),
        ),
        patch.object(
            OpenFigiDetails,
            "filter",
            classmethod(
                lambda cls, **kw: [SimpleNamespace(asset_uid=asset_uids[0], figi="BBG000")]
            ),
        ),
        patch("alpaca_connectors.assets.resolution._alpaca_details_rows", details_rows),
    ):
        result = ticker_and_optional_figi_by_unique_identifiers([*identifiers, identifiers[0]])

    assert result == {
        identifiers[0]: ("AAA", "BBG000"),
        identifiers[1]: ("BBB", None),
        identifiers[2]: (None, None),
    }
    assert len(own_queries) == 1


def test_category_members_are_paged_and_resolved_in_batches() -> None:
    from msm.api.assets import Asset, AssetCategory, AssetCategoryMembership

    category_uid = uuid.uuid4()
    member_uids = [uuid.UUID(int=index + 1) for index in range(501)]
    pages: list[tuple[int, int]] = []

    def memberships(cls=None, **kwargs):
        pages.append((kwargs["limit"], kwargs["offset"]))
        assert kwargs["category_uid"] == str(category_uid)
        chunk = member_uids[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]]
        return [SimpleNamespace(asset_uid=value) for value in chunk]

    with (
        patch.object(
            AssetCategory,
            "get_by_unique_identifier",
            classmethod(lambda cls, value: SimpleNamespace(uid=category_uid)),
        ),
        patch.object(AssetCategoryMembership, "count", classmethod(lambda cls, **kw: 501)),
        patch.object(
            AssetCategoryMembership, "filter", classmethod(lambda cls, **kw: memberships(**kw))
        ),
        patch.object(
            Asset,
            "filter",
            classmethod(lambda cls, **kw: [_asset(uuid.UUID(value)) for value in kw["uid__in"]]),
        ),
    ):
        identifiers = asset_unique_identifiers_for_category("HOLDINGS__IVV")

    assert pages == [(500, 0), (500, 500)]
    assert identifiers == sorted(f"ALPACA::{value}" for value in member_uids)


def test_ticker_lookup_reads_own_details_then_markets_assets() -> None:
    from msm.api.assets import Asset

    asset_uid = uuid.UUID(int=9)
    with (
        patch(
            "alpaca_connectors.assets.resolution._alpaca_details_rows",
            lambda where: [{"asset_uid": asset_uid, "symbol": "IVV"}],
        ),
        patch.object(Asset, "filter", classmethod(lambda cls, **kw: [_asset(asset_uid)])),
    ):
        (asset,) = assets_for_ticker("ivv")

    assert asset.unique_identifier == f"ALPACA::{asset_uid}"
