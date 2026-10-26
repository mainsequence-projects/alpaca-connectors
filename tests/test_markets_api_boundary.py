"""The connector reaches Markets through the Markets API (ms-markets ADR 0047).

The Markets row classes are replaced here by recording fakes, so these tests check what the
adapters ask Markets for (set-based row calls and the publication composites) and how they shape
the answers. No test needs the network.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

ACCOUNT = str(uuid.uuid4())
WHEN = dt.datetime(2026, 3, 5, 16, tzinfo=dt.UTC)


@pytest.fixture
def no_engine(monkeypatch) -> None:
    from alpaca_connectors import runtime

    monkeypatch.setattr(runtime, "start_markets_engine", lambda **kwargs: None)


def _set(uid: str, when: dt.datetime) -> Any:
    return SimpleNamespace(uid=uid, account_uid=ACCOUNT, time_index=when)


def _snapshot(set_uid: str, when: dt.datetime, *identifiers: str) -> dict[str, Any]:
    return {
        "holdings_set_uid": set_uid,
        "holdings_date": when,
        "holdings": [
            {
                "time_index": when.isoformat(),
                "asset_identifier": identifier,
                "quantity": "10",
                "direction": 1,
                "target_trade_time": when.isoformat(),
                "extra_details": {"symbol": identifier[-1]},
            }
            for identifier in identifiers
        ],
    }


def test_holdings_publish_through_the_snapshot_composite(monkeypatch) -> None:
    from msm.api.accounts import AccountHoldingsSnapshots

    from alpaca_connectors.holdings.services import publish_resolved_account_holdings_snapshot

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        AccountHoldingsSnapshots,
        "publish",
        classmethod(lambda cls, **kw: calls.append(kw) or _snapshot("s1", WHEN, "ALPACA::A")),
    )
    rows = [{"asset_identifier": "ALPACA::A", "quantity": 10, "direction": 1, "extra_details": {}}]

    result = publish_resolved_account_holdings_snapshot(
        account_uid=ACCOUNT, rows=rows, snapshot_time=WHEN
    )

    assert (result.holdings_set_uid, result.holdings_rows) == ("s1", 1)
    assert calls == [
        {"account_uid": ACCOUNT, "holdings_date": WHEN, "positions": rows, "overwrite": True}
    ]


def test_holdings_listing_reads_sets_then_snapshots_in_batches(monkeypatch, no_engine) -> None:
    from msm.api.accounts import AccountHoldingsSet, AccountHoldingsSnapshots

    from alpaca_connectors.assets import resolution
    from alpaca_connectors.holdings.services import list_account_holdings

    earlier = WHEN - dt.timedelta(days=1)
    sets = [_set("s2", WHEN), _set("s1", earlier)]
    snapshots = {
        WHEN: _snapshot("s2", WHEN, "ALPACA::B", "ALPACA::A"),
        earlier: _snapshot("s1", earlier, "ALPACA::A"),
    }
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        AccountHoldingsSet, "count", classmethod(lambda cls, **kw: calls.append(("count", kw)) or 2)
    )
    monkeypatch.setattr(
        AccountHoldingsSet,
        "filter",
        classmethod(lambda cls, **kw: calls.append(("filter", kw)) or sets),
    )
    monkeypatch.setattr(
        AccountHoldingsSnapshots,
        "get_many",
        classmethod(
            lambda cls, **kw: (
                calls.append(("get_many", kw))
                or [snapshots[value] for value in kw["holdings_dates"]]
            )
        ),
    )
    monkeypatch.setattr(
        resolution,
        "assets_by_unique_identifiers",
        lambda identifiers: {value: SimpleNamespace(uid=f"uid-{value}") for value in identifiers},
    )

    rows, total = list_account_holdings(ACCOUNT, limit=2, offset=0)

    assert total == 3
    assert [(row["holdings_set_uid"], row["asset_identifier"]) for row in rows] == [
        ("s2", "ALPACA::A"),
        ("s2", "ALPACA::B"),
    ]
    assert rows[0]["quantity"] == 10.0 and rows[0]["asset_uid"] == "uid-ALPACA::A"
    assert calls == [
        ("count", {"account_uid": ACCOUNT}),
        (
            "filter",
            {
                "account_uid": ACCOUNT,
                "order_by": ["-time_index", "-uid"],
                "limit": 500,
                "offset": 0,
            },
        ),
        ("get_many", {"account_uid": ACCOUNT, "holdings_dates": [WHEN, earlier], "limit": 2}),
    ]


def test_recent_holdings_scope_reads_one_set_and_its_snapshot(monkeypatch, no_engine) -> None:
    from msm.api.accounts import AccountHoldingsSet, AccountHoldingsSnapshots

    from alpaca_connectors.assets import resolution
    from alpaca_connectors.holdings.services import resolve_recent_account_holdings_assets

    monkeypatch.setattr(
        AccountHoldingsSet, "filter", classmethod(lambda cls, **kw: [_set("s2", WHEN)])
    )
    monkeypatch.setattr(
        AccountHoldingsSnapshots,
        "get_many",
        classmethod(lambda cls, **kw: [_snapshot("s2", WHEN, "ALPACA::A", "USD")]),
    )
    monkeypatch.setattr(
        resolution,
        "assets_by_unique_identifiers",
        lambda identifiers: {value: SimpleNamespace(uid=f"uid-{value}") for value in identifiers},
    )

    scope = resolve_recent_account_holdings_assets(ACCOUNT, as_of=WHEN)

    assert scope.holdings_set_uid == "s2"
    assert scope.asset_identifiers == ["ALPACA::A"]
    assert scope.asset_uids == ["uid-ALPACA::A"]
