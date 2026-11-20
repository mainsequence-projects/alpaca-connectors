"""Revision 0033 drops the three published series instances (ADR 0031).

It runs here on SQLite through Alembic's own operations, in the batch mode the MetaTables
environment uses for SQLite.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alpaca_connectors.instances import AlpacaPortfolioRebalanceStateStorage
from metatables.migrations.sqlite import AUTO_BATCH_OPTION

REVISIONS = (
    Path(__file__).resolve().parents[1]
    / "src/alpaca_connectors/migrations/versions/alpaca_connectors"
)


def _revision(file_name: str):
    spec = importlib.util.spec_from_file_location(
        f"revision_{file_name[:4]}", REVISIONS / file_name
    )
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


REVISION_0020 = _revision("0020_add_provider_time_series_instances.py")
REVISION_0026 = _revision("0026_add_weights_series_lookup_indexes.py")
REVISION_0033 = _revision("0033_drop_published_series_instances.py")
DROPPED = (
    REVISION_0033.SIGNAL_WEIGHTS_TABLE,
    REVISION_0033.PORTFOLIOS_TABLE,
    REVISION_0033.PORTFOLIO_WEIGHTS_TABLE,
)


def _run(connection: sa.Connection, revision, step: str) -> None:
    operations = Operations(MigrationContext.configure(connection, opts={AUTO_BATCH_OPTION: True}))
    with patch.object(revision, "op", operations):
        getattr(revision, step)()


def _schema_before(connection: sa.Connection) -> None:
    """The Markets registries the instances reference, and the instances as 0032 left them."""
    for statement in (
        "CREATE TABLE ms_markets__asset (unique_identifier VARCHAR(255) PRIMARY KEY)",
        "CREATE TABLE ms_markets__portfolio (unique_identifier VARCHAR(255) PRIMARY KEY)",
        "CREATE TABLE ms_markets__signalmetadata (signal_uid VARCHAR(255) PRIMARY KEY)",
        "CREATE TABLE ms_markets__calendar (unique_identifier VARCHAR(255) PRIMARY KEY)",
    ):
        connection.execute(sa.text(statement))
    _run(connection, REVISION_0020, "upgrade")
    _run(connection, REVISION_0026, "upgrade")


def _indexes(connection: sa.Connection, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(connection).get_indexes(table)}


def test_revision_0033_follows_0032() -> None:
    assert (REVISION_0033.revision, REVISION_0033.down_revision) == ("0033", "0032")


def test_the_series_instances_go_and_the_rebalance_state_gets_its_portfolio_index() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _schema_before(connection)
        before = {name: _indexes(connection, name) for name in DROPPED}
        connection.execute(
            sa.text(
                f"INSERT INTO {REVISION_0033.SIGNAL_WEIGHTS_TABLE} "
                "(time_index, signal_uid, asset_identifier, signal_weight) "
                "VALUES ('2026-11-16 14:00:00+00:00', 's', 'a', 1.0)"
            )
        )

        _run(connection, REVISION_0033, "upgrade")

        tables = set(sa.inspect(connection).get_table_names())
        assert not set(DROPPED) & tables
        rebalance = REVISION_0033.REBALANCE_STATE_TABLE
        assert REVISION_0033.REBALANCE_STATE_PORTFOLIO_INDEX in _indexes(connection, rebalance)
        # The index the model declares, under the name the naming convention gives it.
        declared = {
            index.name: [column.name for column in index.columns]
            for index in AlpacaPortfolioRebalanceStateStorage.__table__.indexes
            if not index.unique
        }
        assert declared == {
            REVISION_0033.REBALANCE_STATE_PORTFOLIO_INDEX: ["portfolio_identifier", "time_index"]
        }

        # The downgrade recreates them empty, with revisions 0020's and 0026's indexes.
        _run(connection, REVISION_0033, "downgrade")

        assert set(DROPPED) <= set(sa.inspect(connection).get_table_names())
        assert {name: _indexes(connection, name) for name in DROPPED} == before
        rows = connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {REVISION_0033.SIGNAL_WEIGHTS_TABLE}")
        ).scalar()
        assert rows == 0
        assert REVISION_0033.REBALANCE_STATE_PORTFOLIO_INDEX not in _indexes(
            connection, REVISION_0033.REBALANCE_STATE_TABLE
        )
