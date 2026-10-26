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


def _compiled_sql(statement: Any) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def test_account_listing_pages_the_details_table_and_hydrates_accounts(monkeypatch) -> None:
    from msm.api.accounts import Account

    from alpaca_connectors.account.services import list_account_registrations

    account_uid = uuid.uuid4()
    detail = {
        "account_uid": account_uid,
        "account_unique_identifier": "PA1__ALPACA_PAPER",
        "account_name": "stale copy",
        "account_is_active": True,
        "is_paper": True,
        "status": "ACTIVE",
    }
    statements: list[Any] = []
    monkeypatch.setattr(
        "alpaca_connectors.runtime.start_markets_engine",
        lambda **kwargs: SimpleNamespace(context=object()),
    )
    monkeypatch.setattr(
        "msm.repositories.base.compile_markets_statement",
        lambda statement, **kwargs: statements.append(statement) or statement,
    )
    results = iter([{"rows": [detail]}, {"rows": [{"count": 1}]}])
    monkeypatch.setattr(
        "msm.repositories.base.execute_markets_operation", lambda operation, **kwargs: next(results)
    )
    filters: list[dict[str, Any]] = []
    monkeypatch.setattr(
        Account,
        "filter",
        classmethod(
            lambda cls, **kw: (
                filters.append(kw)
                or [
                    SimpleNamespace(
                        uid=account_uid,
                        model_dump=lambda mode="json": {
                            "uid": str(account_uid),
                            "unique_identifier": "PA1__ALPACA_PAPER",
                            "account_name": "Paper One",
                            "account_is_active": True,
                        },
                    )
                ]
            )
        ),
    )

    rows, total = list_account_registrations(search="paper", active=True)

    assert total == 1
    assert rows[0]["account_name"] == "Paper One"
    assert rows[0]["unique_identifier"] == "PA1__ALPACA_PAPER"
    assert rows[0]["uid"] == rows[0]["account_uid"] == str(account_uid)
    assert filters == [{"uid__in": [str(account_uid)], "limit": 1}]
    for statement in statements:
        sql = _compiled_sql(statement)
        assert "ms_markets__" not in sql
        assert "alpaca_connectors__acct_alpaca" in sql
    # The encrypted key columns are never selected, only the credentials_stored flag.
    assert "api_key AS api_key" not in _compiled_sql(statements[0])


def test_listing_field_backfill_updates_changed_rows_in_one_statement(monkeypatch) -> None:
    from msm.api.accounts import Account

    from alpaca_connectors.account.services import backfill_account_listing_fields

    current, stale = uuid.uuid4(), uuid.uuid4()
    rows = [
        {"account_uid": current, "account_name": "A", "account_is_active": True},
        {"account_uid": stale, "account_name": None, "account_is_active": None},
    ]
    executed: list[Any] = []
    monkeypatch.setattr(
        "alpaca_connectors.runtime.start_markets_engine",
        lambda **kwargs: SimpleNamespace(context=object()),
    )
    monkeypatch.setattr(
        "msm.repositories.base.compile_markets_statement", lambda statement, **kwargs: statement
    )
    monkeypatch.setattr(
        "msm.repositories.base.execute_markets_operation",
        lambda statement, **kwargs: executed.append(statement) or {"rows": rows},
    )
    monkeypatch.setattr(
        Account,
        "filter",
        classmethod(
            lambda cls, **kw: [
                SimpleNamespace(uid=current, account_name="A", account_is_active=True),
                SimpleNamespace(uid=stale, account_name="B", account_is_active=False),
            ]
        ),
    )

    dry_run = backfill_account_listing_fields()
    assert dry_run["would_update"] == [str(stale)] and len(executed) == 1

    executed.clear()
    result = backfill_account_listing_fields(execute=True)

    assert result["updated"] == [str(stale)]
    select_statement, update_statement = executed
    sql = _compiled_sql(update_statement)
    assert sql.startswith("UPDATE alpaca_connectors__acct_alpaca")
    assert "CASE" in sql and stale.hex in sql and current.hex not in sql
