"""Revision 0036 re-keys the orders and trades instances on ms-markets 6.0.0 (its ADR 0064).

It runs here on SQLite through Alembic's own operations, in the batch mode the MetaTables
environment uses for SQLite, over the two tables as revision 0020 created them. The Markets API
calls (``Order.upsert_many`` and the downgrade's ``Order`` read) are stood in for.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from msm.time_index.execution.upgrade import order_uid_for_identifier

from alpaca_connectors.execution.orders import OrderRecord
from alpaca_connectors.instances import (
    AlpacaOrderEventsStorage,
    AlpacaOrdersStorage,
    AlpacaTradesStorage,
)
from metatables.migrations.sqlite import AUTO_BATCH_OPTION

REVISIONS = (
    Path(__file__).resolve().parents[1]
    / "src/alpaca_connectors/migrations/versions/alpaca_connectors"
)
spec = importlib.util.spec_from_file_location(
    "revision_0036", REVISIONS / "0036_an_order_has_its_own_row.py"
)
REVISION = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REVISION)

T1 = dt.datetime(2026, 11, 20, 15, tzinfo=dt.UTC)
T2 = T1 + dt.timedelta(minutes=5)

# The Markets tables the keys reference, which the SQLite runtime holds in the same database.
MARKETS_SCHEMA = (
    "CREATE TABLE ms_markets__account (unique_identifier VARCHAR(255) PRIMARY KEY)",
    "CREATE TABLE ms_markets__asset (unique_identifier VARCHAR(255) PRIMARY KEY)",
    "CREATE TABLE ms_markets__ordermanager (unique_identifier VARCHAR(255) PRIMARY KEY)",
    "CREATE TABLE ms_markets__order (uid CHAR(32) PRIMARY KEY, "
    "account_identifier VARCHAR(255), asset_identifier VARCHAR(255), "
    "UNIQUE (uid, account_identifier, asset_identifier))",
)
# The two tables as revision 0020 created them, with its index and key names.
LEGACY_SCHEMA = (
    f"""CREATE TABLE {REVISION.ORDERS} (
        order_time DATETIME NOT NULL, order_identifier VARCHAR NOT NULL,
        account_identifier VARCHAR(255) NOT NULL, order_manager_identifier VARCHAR(255),
        asset_identifier VARCHAR(255) NOT NULL, order_remote_id VARCHAR, client_order_id VARCHAR,
        order_type VARCHAR, order_side BIGINT, quantity FLOAT, status VARCHAR,
        filled_quantity FLOAT, filled_price FLOAT, expires_time DATETIME, limit_price FLOAT,
        time_in_force VARCHAR, comments VARCHAR, venue_metadata JSON,
        CONSTRAINT {REVISION.FORMER_ORDERS_KEYS[0][0]} FOREIGN KEY(account_identifier)
            REFERENCES ms_markets__account (unique_identifier) ON DELETE RESTRICT,
        CONSTRAINT {REVISION.FORMER_ORDERS_KEYS[1][0]} FOREIGN KEY(asset_identifier)
            REFERENCES ms_markets__asset (unique_identifier) ON DELETE RESTRICT,
        CONSTRAINT {REVISION.FORMER_ORDERS_KEYS[2][0]} FOREIGN KEY(order_manager_identifier)
            REFERENCES ms_markets__ordermanager (unique_identifier) ON DELETE RESTRICT)""",
    f"CREATE UNIQUE INDEX {REVISION.FORMER_ORDERS_GRAIN_INDEX} ON {REVISION.ORDERS} "
    "(order_time, order_identifier, account_identifier, asset_identifier)",
    f"""CREATE TABLE {REVISION.TRADES} (
        trade_time DATETIME NOT NULL, trade_identifier VARCHAR NOT NULL,
        account_identifier VARCHAR(255) NOT NULL, order_identifier VARCHAR,
        asset_identifier VARCHAR(255) NOT NULL, trade_side BIGINT, quantity FLOAT, price FLOAT,
        commission FLOAT, commission_asset_identifier VARCHAR(255), settlement_cost FLOAT,
        settlement_asset_identifier VARCHAR(255), comments VARCHAR, venue_metadata JSON,
        CONSTRAINT fk__alpaca_connectors__tradests__main__account_ident_fde1b7d0c8
            FOREIGN KEY(account_identifier) REFERENCES ms_markets__account (unique_identifier))""",
    f"CREATE UNIQUE INDEX {REVISION.FORMER_TRADES_GRAIN_INDEX} ON {REVISION.TRADES} "
    "(trade_time, trade_identifier, account_identifier, asset_identifier)",
)


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection, opts={AUTO_BATCH_OPTION: True}))


def _state(connection, **values) -> None:
    row = {
        "order_time": T1,
        "order_identifier": "c-1",
        "account_identifier": "ACCOUNT",
        "order_manager_identifier": "BATCH",
        "asset_identifier": "ALPACA::a",
        "client_order_id": "c-1",
        "order_type": "market",
        "order_side": 1,
        "quantity": 2.0,
        "status": "filled",
        "filled_quantity": 2.0,
        "filled_price": 10.0,
        "time_in_force": "day",
        **values,
    }
    columns = ", ".join(row)
    connection.execute(
        sa.text(
            f"INSERT INTO {REVISION.ORDERS} ({columns}) "
            f"VALUES ({', '.join(':' + name for name in row)})"
        ),
        row,
    )


def _trade(connection, trade_identifier: str, order_identifier: str) -> None:
    connection.execute(
        sa.text(
            f"INSERT INTO {REVISION.TRADES} (trade_time, trade_identifier, account_identifier, "
            "order_identifier, asset_identifier, quantity) "
            "VALUES (:time, :trade, 'ACCOUNT', :order, 'ALPACA::a', 2.0)"
        ),
        {"time": T2, "trade": trade_identifier, "order": order_identifier},
    )


def _columns(connection, table: str) -> list[str]:
    return [column["name"] for column in sa.inspect(connection).get_columns(table)]


@pytest.fixture
def connection():
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        for statement in (*MARKETS_SCHEMA, *LEGACY_SCHEMA):
            connection.execute(sa.text(statement))
        yield connection


def test_revision_0036_follows_0035_and_names_the_instances_tables() -> None:
    assert (REVISION.revision, REVISION.down_revision) == ("0036", "0035")
    assert (REVISION.ORDERS, REVISION.TRADES, REVISION.EVENTS) == (
        AlpacaOrdersStorage.__table__.name,
        AlpacaTradesStorage.__table__.name,
        AlpacaOrderEventsStorage.__table__.name,
    )
    # The index and key names it creates are the ones the instances declare.
    for table, index, key in (
        (AlpacaOrdersStorage, REVISION.ORDERS_GRAIN_INDEX, REVISION.ORDERS_ORDER_KEY),
        (AlpacaTradesStorage, REVISION.TRADES_GRAIN_INDEX, REVISION.TRADES_ORDER_KEY),
        (AlpacaOrderEventsStorage, REVISION.EVENTS_GRAIN_INDEX, REVISION.EVENTS_ORDER_KEY),
    ):
        assert {i.name for i in table.__table__.indexes} == {index}
        assert key in {c.name for c in table.__table__.foreign_key_constraints}


def test_upgrade_gives_each_order_its_row_and_keys_its_states_and_trades_by_it(
    connection,
) -> None:
    _state(connection)
    # A later state of the same order: its Order row takes the earliest one's terms.
    _state(connection, order_time=T2, quantity=3.0)
    _state(connection, order_identifier="c-2", client_order_id="c-2", status="submitted")
    _trade(connection, "c-1:fill", "c-1")
    # A trade whose order has no state row keeps naming its order.
    _trade(connection, "c-9:fill", "c-9")

    with (
        patch.object(REVISION, "op", _operations(connection)),
        patch.object(REVISION, "_record_orders") as record_orders,
    ):
        REVISION.upgrade()

    [orders] = record_orders.call_args.args
    by_uid = {order["uid"]: order for order in orders}
    first = by_uid[order_uid_for_identifier("c-1")]
    assert first["quantity"] == 2.0 and first["order_manager_identifier"] == "BATCH"
    assert first["account_identifier"] == "ACCOUNT" and first["asset_identifier"] == "ALPACA::a"
    assert set(by_uid) == {order_uid_for_identifier(i) for i in ("c-1", "c-2", "c-9")}
    assert by_uid[order_uid_for_identifier("c-9")]["client_order_id"] == "c-9"

    assert set(_columns(connection, REVISION.ORDERS)) == {
        column.name for column in AlpacaOrdersStorage.__table__.columns
    }
    assert set(_columns(connection, REVISION.TRADES)) == {
        column.name for column in AlpacaTradesStorage.__table__.columns
    }
    assert set(_columns(connection, REVISION.EVENTS)) == {
        column.name for column in AlpacaOrderEventsStorage.__table__.columns
    }
    states = connection.execute(sa.text(f"SELECT order_uid FROM {REVISION.ORDERS}")).scalars()
    assert sorted(uuid.UUID(str(uid)) for uid in states) == sorted(
        [order_uid_for_identifier("c-1")] * 2 + [order_uid_for_identifier("c-2")]
    )
    trades = dict(
        connection.execute(
            sa.text(f"SELECT trade_identifier, order_uid FROM {REVISION.TRADES}")
        ).all()
    )
    assert {key: uuid.UUID(str(value)) for key, value in trades.items()} == {
        "c-1:fill": order_uid_for_identifier("c-1"),
        "c-9:fill": order_uid_for_identifier("c-9"),
    }


def test_an_empty_ledger_needs_no_markets_call(connection) -> None:
    with (
        patch.object(REVISION, "op", _operations(connection)),
        patch.object(REVISION, "_record_orders") as record_orders,
    ):
        REVISION.upgrade()
    record_orders.assert_not_called()


def test_downgrade_puts_the_orders_columns_back_from_their_rows(connection) -> None:
    _state(connection)
    _trade(connection, "c-1:fill", "c-1")
    uid = order_uid_for_identifier("c-1")
    order = SimpleNamespace(
        uid=uid,
        account_identifier="ACCOUNT",
        asset_identifier="ALPACA::a",
        order_manager_identifier="BATCH",
        order_side=1,
        order_type="market",
        quantity=2.0,
        limit_price=None,
        time_in_force="day",
        expires_time=None,
        client_order_id="c-1",
    )
    with (
        patch.object(REVISION, "op", _operations(connection)),
        patch.object(REVISION, "_record_orders"),
        patch.object(REVISION, "_read_orders", return_value={uid: order}) as read_orders,
    ):
        REVISION.upgrade()
        REVISION.downgrade()

    assert [uuid.UUID(str(u)) for u in read_orders.call_args.args[0]] == [uid]
    assert "order_uid" not in _columns(connection, REVISION.ORDERS)
    assert REVISION.EVENTS not in sa.inspect(connection).get_table_names()
    row = connection.execute(
        sa.text(
            f"SELECT order_identifier, account_identifier, order_manager_identifier, quantity "
            f"FROM {REVISION.ORDERS}"
        )
    ).one()
    assert tuple(row) == ("c-1", "ACCOUNT", "BATCH", 2.0)
    trade = connection.execute(sa.text(f"SELECT order_identifier FROM {REVISION.TRADES}")).scalar()
    assert trade == "c-1"


def test_a_batch_recorded_before_6_0_0_names_its_orders_by_the_revisions_uids() -> None:
    record = OrderRecord.from_payload(
        {
            "client_order_id": "c-1",
            "asset_identifier": "A",
            "symbol": "A",
            "side": "buy",
            "quantity": 1.0,
        }
    )
    assert record.order_uid == str(order_uid_for_identifier("c-1"))
    minted = str(uuid.uuid4())
    assert (
        OrderRecord.from_payload({**record.to_payload(), "order_uid": minted}).order_uid == minted
    )
