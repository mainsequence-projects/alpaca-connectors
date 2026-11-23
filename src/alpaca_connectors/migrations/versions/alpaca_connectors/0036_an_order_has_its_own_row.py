"""an order has its own row: re-key the orders and trades instances on ms-markets 6.0.0

ms-markets 6.0.0 (its ADR 0064) gives each order its own Markets ``Order`` row, keyed by the
order's uid, with its account, asset, batch and terms. ``OrdersTS`` holds only the order's state,
keyed by ``(order_time, order_uid)``, and ``TradesTS`` is keyed by ``(trade_time,
trade_identifier)``, a trade naming its order by ``(order_uid, account_identifier,
asset_identifier)``. Markets' revision 0037 migrated Markets' own tables; this revision migrates
this application's instances the same way, as the ms-markets ``execution_workflow`` skill says:

1. The orders already stored get their ``Order`` rows first, through the Markets API
   (``Order.upsert_many``): each ``order_identifier`` becomes
   ``order_uid_for_identifier(identifier)``, exactly as revision 0037 maps Markets' own, and an
   order takes its account, asset, batch and terms from its earliest state row. A trade whose
   order has no state row gets an ``Order`` row of its own account and asset, so it keeps naming
   its order.
2. Both tables get ``order_uid``; ``OrdersTS`` drops the columns that moved to ``Order``,
   ``TradesTS`` its ``order_identifier``; each gets the 6.0.0 unique index and the keys into
   ``Order``.
3. ``alpaca_connectors__ordereventsts__main``, the ``OrderEventsTS`` instance, is created:
   ``write_execution`` writes into an application's instances of all three tables.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-23 20:00:00.000000

"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0036"
down_revision: Union[str, Sequence[str], None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORDER = "ms_markets__order"
ORDERS = "alpaca_connectors__ordersts__main"
TRADES = "alpaca_connectors__tradests__main"
EVENTS = "alpaca_connectors__ordereventsts__main"

ORDER_TERMS = (
    "order_side",
    "order_type",
    "quantity",
    "limit_price",
    "time_in_force",
    "expires_time",
    "client_order_id",
)
# The columns of the pre-6.0.0 OrdersTS row that are the order's own, now on its Order row.
MOVED_COLUMNS = {
    "order_identifier": sa.String(),
    "account_identifier": sa.String(length=255),
    "order_manager_identifier": sa.String(length=255),
    "asset_identifier": sa.String(length=255),
    "client_order_id": sa.String(),
    "order_type": sa.String(),
    "order_side": sa.BigInteger(),
    "quantity": sa.Float(),
    "expires_time": sa.DateTime(timezone=True),
    "limit_price": sa.Float(),
    "time_in_force": sa.String(),
}

ORDERS_GRAIN_INDEX = "uix__alpaca_connectors__ordersts__main__order_time__order_uid"
ORDERS_ORDER_KEY = "fk__alpaca_connectors__ordersts__main__order_uid__ms_d203952297"
FORMER_ORDERS_GRAIN_INDEX = "uix__alpaca_connectors__ordersts__main__order_time_458db1c85f"
FORMER_ORDERS_GRAIN = ["order_time", "order_identifier", "account_identifier", "asset_identifier"]
FORMER_ORDERS_KEYS = (
    (
        "fk__alpaca_connectors__ordersts__main__account_ident_65b5d6638c",
        "account_identifier",
        "ms_markets__account",
    ),
    (
        "fk__alpaca_connectors__ordersts__main__asset_identif_d501258745",
        "asset_identifier",
        "ms_markets__asset",
    ),
    (
        "fk__alpaca_connectors__ordersts__main__order_manager_def90dbef0",
        "order_manager_identifier",
        "ms_markets__ordermanager",
    ),
)

TRADES_GRAIN_INDEX = "uix__alpaca_connectors__tradests__main__trade_time_2e5e21f29d"
TRADES_ORDER_KEY = "fk__alpaca_connectors__tradests__main__order_uid__ac_1adff5d46b"
FORMER_TRADES_GRAIN_INDEX = "uix__alpaca_connectors__tradests__main__trade_time_de2a678e3f"
FORMER_TRADES_GRAIN = ["trade_time", "trade_identifier", "account_identifier", "asset_identifier"]

EVENTS_GRAIN_INDEX = "uix__alpaca_connectors__ordereventsts__main__event_t_4b1cf37d9d"
EVENTS_ORDER_KEY = "fk__alpaca_connectors__ordereventsts__main__order_ui_b825ec3850"


def _record_orders(orders: list[dict[str, Any]]) -> None:
    """Give the orders their ``Order`` rows through the Markets API (an ``msm.api`` row call)."""
    from msm.api import Order

    from alpaca_connectors.runtime import use_markets_api

    use_markets_api()
    Order.upsert_many(orders)


def _read_orders(order_uids: list[Any]) -> dict[Any, Any]:
    """The ``Order`` rows of ``order_uids``, read through the Markets API, by uid."""
    from msm.api import Order

    from alpaca_connectors.markets_api import read_in_batches
    from alpaca_connectors.runtime import use_markets_api

    use_markets_api()
    rows = read_in_batches(lambda batch: Order.filter(uid__in=batch, limit=len(batch)), order_uids)
    return {row.uid: row for row in rows}


# Upgrade ------------------------------------------------------------------------------------


def _orders_of_trades(trades: Iterable[Mapping[str, Any]], known: set[Any]) -> list[dict[str, Any]]:
    """An ``Order`` row for each order a trade names that has no state row: its trade's account
    and asset, the order's identifier as its ``client_order_id``."""
    from msm.time_index.execution.upgrade import order_uid_for_identifier

    orders: dict[Any, dict[str, Any]] = {}
    for trade in trades:
        uid = order_uid_for_identifier(str(trade["order_identifier"]))
        if uid not in known and uid not in orders:
            orders[uid] = {
                "uid": uid,
                "account_identifier": trade["account_identifier"],
                "asset_identifier": trade["asset_identifier"],
                "client_order_id": str(trade["order_identifier"]),
            }
    return list(orders.values())


def _set_order_uids(bind: Any, table: str, identifiers: Iterable[str]) -> None:
    from msm.time_index.execution.upgrade import order_uid_for_identifier

    rows = [
        {"order_uid": order_uid_for_identifier(identifier), "order_identifier": identifier}
        for identifier in sorted(set(identifiers))
    ]
    if rows:
        target = sa.table(
            table, sa.column("order_uid", sa.Uuid()), sa.column("order_identifier", sa.String())
        )
        bind.execute(
            sa.update(target)
            .where(target.c.order_identifier == sa.bindparam("b_identifier"))
            .values(order_uid=sa.bindparam("b_uid")),
            [{"b_uid": row["order_uid"], "b_identifier": row["order_identifier"]} for row in rows],
        )


def upgrade() -> None:
    from msm.time_index.execution.upgrade import orders_from_legacy_states

    bind = op.get_bind()
    columns = ", ".join(
        (
            "order_time",
            "order_identifier",
            "account_identifier",
            "asset_identifier",
            "order_manager_identifier",
            *ORDER_TERMS,
        )
    )
    states = list(bind.execute(sa.text(f"SELECT {columns} FROM {ORDERS}")).mappings())
    trades = list(
        bind.execute(
            sa.text(
                f"SELECT order_identifier, account_identifier, asset_identifier FROM {TRADES} "
                "WHERE order_identifier IS NOT NULL AND order_identifier <> ''"
            )
        ).mappings()
    )
    orders = orders_from_legacy_states(states)
    orders += _orders_of_trades(trades, {order["uid"] for order in orders})
    if orders:
        _record_orders(orders)

    for table in (ORDERS, TRADES):
        with op.batch_alter_table(table, schema=None, recreate="never") as batch_op:
            batch_op.add_column(sa.Column("order_uid", sa.Uuid(), nullable=True))
    _set_order_uids(bind, ORDERS, (str(row["order_identifier"]) for row in states))
    _set_order_uids(bind, TRADES, (str(row["order_identifier"]) for row in trades))

    with op.batch_alter_table(ORDERS, schema=None) as batch_op:
        batch_op.drop_index(FORMER_ORDERS_GRAIN_INDEX)
        for key, _column, _target in FORMER_ORDERS_KEYS:
            batch_op.drop_constraint(key, type_="foreignkey")
        for column in MOVED_COLUMNS:
            batch_op.drop_column(column)
        batch_op.alter_column("order_uid", existing_type=sa.Uuid(), nullable=False)
        batch_op.create_foreign_key(
            ORDERS_ORDER_KEY, ORDER, ["order_uid"], ["uid"], ondelete="RESTRICT"
        )
        batch_op.create_index(ORDERS_GRAIN_INDEX, ["order_time", "order_uid"], unique=True)

    with op.batch_alter_table(TRADES, schema=None) as batch_op:
        batch_op.drop_index(FORMER_TRADES_GRAIN_INDEX)
        batch_op.drop_column("order_identifier")
        batch_op.create_foreign_key(
            TRADES_ORDER_KEY,
            ORDER,
            ["order_uid", "account_identifier", "asset_identifier"],
            ["uid", "account_identifier", "asset_identifier"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(TRADES_GRAIN_INDEX, ["trade_time", "trade_identifier"], unique=True)

    op.create_table(
        EVENTS,
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_uid", sa.Uuid(), nullable=False),
        sa.Column("order_status", sa.String(), nullable=True),
        sa.Column("event_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_uid"], [f"{ORDER}.uid"], name=EVENTS_ORDER_KEY, ondelete="RESTRICT"
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.OrderEventsTS.main",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(EVENTS_GRAIN_INDEX, EVENTS, ["event_time", "order_uid"], unique=True)


# Downgrade ----------------------------------------------------------------------------------


def downgrade() -> None:
    """Put each order's identifier, account, asset, batch and terms back on its state rows, and
    its identifier on its trades, from its ``Order`` row; drop the events instance. The ``Order``
    rows stay in Markets: an order is deleted only with its account's history."""
    bind = op.get_bind()
    op.drop_index(EVENTS_GRAIN_INDEX, table_name=EVENTS)
    op.drop_table(EVENTS)

    order_uids = sorted(
        {
            row[0]
            for table in (ORDERS, TRADES)
            for row in bind.execute(
                sa.text(f"SELECT DISTINCT order_uid FROM {table} WHERE order_uid IS NOT NULL")
            )
        },
        key=str,
    )
    orders = _read_orders(order_uids)

    with op.batch_alter_table(ORDERS, schema=None, recreate="never") as batch_op:
        for column, column_type in MOVED_COLUMNS.items():
            batch_op.add_column(sa.Column(column, column_type, nullable=True))
    with op.batch_alter_table(TRADES, schema=None, recreate="never") as batch_op:
        batch_op.add_column(sa.Column("order_identifier", sa.String(), nullable=True))

    # Bound as ``b_<column>``: SQLAlchemy keeps a column's own name for its SET clause.
    restored = [
        {
            "b_uid": uid,
            "b_order_identifier": order.client_order_id or str(uid),
            "b_account_identifier": order.account_identifier,
            "b_order_manager_identifier": order.order_manager_identifier,
            "b_asset_identifier": order.asset_identifier,
            **{f"b_{term}": getattr(order, term) for term in ORDER_TERMS},
        }
        for uid, order in orders.items()
    ]
    if restored:
        states = sa.table(
            ORDERS,
            sa.column("order_uid", sa.Uuid()),
            *(sa.column(column, column_type) for column, column_type in MOVED_COLUMNS.items()),
        )
        bind.execute(
            sa.update(states)
            .where(states.c.order_uid == sa.bindparam("b_uid"))
            .values({column: sa.bindparam(f"b_{column}") for column in MOVED_COLUMNS}),
            restored,
        )
        trades = sa.table(
            TRADES, sa.column("order_uid", sa.Uuid()), sa.column("order_identifier", sa.String())
        )
        bind.execute(
            sa.update(trades)
            .where(trades.c.order_uid == sa.bindparam("b_uid"))
            .values(order_identifier=sa.bindparam("b_order_identifier")),
            [
                {"b_uid": row["b_uid"], "b_order_identifier": row["b_order_identifier"]}
                for row in restored
            ],
        )

    with op.batch_alter_table(TRADES, schema=None) as batch_op:
        batch_op.drop_index(TRADES_GRAIN_INDEX)
        batch_op.drop_constraint(TRADES_ORDER_KEY, type_="foreignkey")
        batch_op.drop_column("order_uid")
        batch_op.create_index(FORMER_TRADES_GRAIN_INDEX, FORMER_TRADES_GRAIN, unique=True)

    with op.batch_alter_table(ORDERS, schema=None) as batch_op:
        batch_op.drop_index(ORDERS_GRAIN_INDEX)
        batch_op.drop_constraint(ORDERS_ORDER_KEY, type_="foreignkey")
        batch_op.drop_column("order_uid")
        for column in ("order_identifier", "account_identifier", "asset_identifier"):
            batch_op.alter_column(column, existing_type=MOVED_COLUMNS[column], nullable=False)
        for key, column, target in FORMER_ORDERS_KEYS:
            batch_op.create_foreign_key(
                key, target, [column], ["unique_identifier"], ondelete="RESTRICT"
            )
        batch_op.create_index(FORMER_ORDERS_GRAIN_INDEX, FORMER_ORDERS_GRAIN, unique=True)
