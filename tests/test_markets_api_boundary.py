"""The connector reaches Markets through the Markets API (ms-markets ADR 0047).

The Markets row classes are replaced here by recording fakes, so these tests check what the
adapters ask Markets for (set-based row calls and the publication composites) and how they shape
the answers. No test needs the network.
"""

from __future__ import annotations

import datetime as dt
import re
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


def test_start_markets_engine_selects_the_api_and_attaches_only_own_tables(monkeypatch) -> None:
    import msm
    import msm.bootstrap
    from msm.api import remote

    from alpaca_connectors import runtime

    attached: dict[str, Any] = {}
    monkeypatch.setattr(remote, "active_remote_backend", lambda: None)
    monkeypatch.setattr(
        remote, "use_remote_backend", lambda backend: attached.setdefault("backend", backend)
    )
    monkeypatch.setattr(
        "msm.client.MarketsClient.from_environment",
        classmethod(lambda cls: pytest.fail("the Markets API is found on the first row call")),
    )

    def resolve_runtime(**kwargs):
        raise RuntimeError("requires an initialized markets runtime")

    monkeypatch.setattr(msm.bootstrap, "resolve_runtime", resolve_runtime)
    monkeypatch.setattr(
        msm, "start_engine", lambda **kwargs: attached.setdefault("models", kwargs["models"])
    )

    runtime.start_markets_engine()

    from msm.client import LazyRemoteBackend

    assert isinstance(attached["backend"], LazyRemoteBackend)
    names = {model.__table__.name for model in attached["models"]}
    assert all(name.startswith("alpaca_connectors__") for name in names), names
    assert {"alpaca_connectors__acct_alpaca", "alpaca_connectors__asset_alpaca"} <= names


def test_producers_write_to_own_namespace_instances() -> None:
    from alpaca_connectors import instances
    from alpaca_connectors.portfolios.interpolated_prices_schema import (
        configured_alpaca_interpolated_prices_storage,
    )

    for model in instances.project_instance_models():
        assert model.__table__.info["namespace"] == "alpaca-connectors"
        assert model.__table__.name.startswith("alpaca_connectors__")
        assert model.__table__.name.endswith("__main")
    assert instances.portfolio_output_tables() == {
        "output_table": instances.AlpacaPortfoliosStorage,
        "weights_output_table": instances.AlpacaPortfolioWeightsStorage,
        "rebalance_output_table": instances.AlpacaPortfolioRebalanceStateStorage,
    }
    interpolated = configured_alpaca_interpolated_prices_storage(
        source_time_index_meta_table_uid="11111111-1111-4111-8111-111111111111",
        source_cadence="1d",
    )
    assert interpolated.__table__.name.startswith("alpaca_connectors__interpolated_prices__t_")
    assert interpolated.__table__.info["namespace"] == "alpaca-connectors"


def test_signal_and_ledger_updaters_are_bound_to_own_instances(monkeypatch) -> None:
    from alpaca_connectors.execution.services import MarketsExecutionLedger
    from alpaca_connectors.instances import (
        AlpacaOrdersStorage,
        AlpacaSignalWeightsStorage,
        AlpacaTradesStorage,
    )
    from alpaca_connectors.portfolios.alpaca_etf_signal import (
        AlpacaETFHoldingsSignal,
        build_alpaca_etf_holdings_signal,
    )

    built: dict[str, Any] = {}
    monkeypatch.setattr(
        AlpacaETFHoldingsSignal,
        "from_signal_configuration",
        classmethod(lambda cls, config, **kwargs: built.setdefault("signal", kwargs)),
    )
    build_alpaca_etf_holdings_signal(universe_uid="universe", account_uid="account")
    assert built["signal"] == {"output_table": AlpacaSignalWeightsStorage}

    class Node:
        tables: list[Any] = []

        def __init__(self, config=None, output_table=None):
            Node.tables.append(output_table)

        @classmethod
        def default_config(cls):
            return None

        def set_frame(self, frame):
            return self

        def run(self):
            return False, None

    monkeypatch.setattr("msm.time_index.execution.Orders", Node)
    monkeypatch.setattr("msm.time_index.execution.Trades", Node)
    ledger = MarketsExecutionLedger()
    ledger.write_orders([{"order_identifier": "o"}])
    ledger.write_trades([{"trade_identifier": "t"}])
    assert Node.tables == [AlpacaOrdersStorage, AlpacaTradesStorage]


def test_portfolio_catalog_registration_names_the_own_instances(monkeypatch) -> None:
    from msm_portfolios.api.datasets import PortfolioDatasets

    from alpaca_connectors import instances
    from alpaca_connectors.portfolios import datasets

    for index, model in enumerate(
        (
            instances.AlpacaPortfoliosStorage,
            instances.AlpacaPortfolioWeightsStorage,
            instances.AlpacaSignalWeightsStorage,
        )
    ):
        monkeypatch.setattr(
            model, "get_meta_table_uid", classmethod(lambda cls, index=index: f"table-{index}")
        )
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        PortfolioDatasets,
        "register",
        classmethod(lambda cls, registration, **kw: calls.append(("register", registration, kw))),
    )
    monkeypatch.setattr(
        PortfolioDatasets,
        "register_signal",
        classmethod(lambda cls, registration: calls.append(("register_signal", registration))),
    )

    datasets.register_portfolio_datasets("portfolio-uid")
    datasets.register_signal_dataset("signal-uid")

    assert calls == [
        (
            "register",
            {
                "portfolio_uid": "portfolio-uid",
                "values_meta_table_uid": "table-0",
                "weights_meta_table_uid": "table-1",
                "signal_meta_table_uid": "table-2",
            },
            {"reconcile": True},
        ),
        ("register_signal", {"signal_uid": "signal-uid", "signal_meta_table_uid": "table-2"}),
    ]


# Markets table classes may appear only where this application declares a foreign key to a
# Markets registry (`X.__table__.fullname` inside ForeignKey(...)), in migration metadata, as a
# contract handed to an ms-markets instance factory, or in the documented one-off backfill.
_MARKETS_TABLE_MODULES = re.compile(
    r"^(msm\.models(\..*)?|msm\.time_index\..*storage|msm_portfolios\.models(\..*)?"
    r"|msm_portfolios\.time_index\..*storage)$"
)
_FOREIGN_KEY_ONLY = {
    "account/alpaca_account_details.py",
    "assets/alpaca_asset_details.py",
    "market_data/configurations.py",
    "market_data/storage.py",
    "operations/signal_job_configurations.py",
    "portfolios/configurations.py",
    "universes/registry.py",
}
_EXEMPT = {
    # Metadata closure for this provider's foreign keys; never queried.
    "migrations/__init__.py",
    "portfolios/interpolated_prices_migration.py",
    # Contracts handed to ms-markets instance factories.
    "instances.py",
    "portfolios/interpolated_prices_schema.py",
    # The one-off copy out of the shared Markets time series, run as a person.
    "operations/instance_backfill.py",
}


def test_no_module_builds_a_statement_on_a_markets_table() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "alpaca_connectors"
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("migrations/versions/"):
            continue
        tree = ast.parse(path.read_text())
        imported = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and _MARKETS_TABLE_MODULES.match(node.module or "")
            for alias in node.names
        }
        if not imported or relative in _EXEMPT:
            continue
        if relative not in _FOREIGN_KEY_ONLY:
            violations.append(f"{relative} imports {sorted(imported)}")
            continue
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Name) and node.id in imported):
                continue
            parent = parents.get(node)
            grandparent = parents.get(parent)
            if not (
                isinstance(parent, ast.Attribute)
                and parent.attr == "__table__"
                and isinstance(grandparent, ast.Attribute)
                and grandparent.attr == "fullname"
            ):
                violations.append(f"{relative}:{node.lineno} uses {node.id}")
    assert violations == [], violations


def test_markets_registries_are_reached_only_through_row_calls() -> None:
    """No module calls the ms-markets repositories or services on Markets tables directly."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "alpaca_connectors"
    allowed_repository_names = {
        # Compile and execute statements on this application's own tables.
        "compile_markets_statement",
        "execute_markets_operation",
        # Generic CRUD on this application's own row tables.
        "bulk_upsert_model",
        "create_model",
        "delete_model",
        "update_model",
        "upsert_model",
    }
    violations = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if module.startswith(("msm.repositories", "msm.services", "msm_portfolios.services")):
                names = {alias.name for alias in node.names} - allowed_repository_names
                if names:
                    violations.append(f"{path.name}: {module} {sorted(names)}")
    assert violations == [], violations
