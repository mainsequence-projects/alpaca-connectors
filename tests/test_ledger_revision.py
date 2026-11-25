"""Revision 0037 creates the event ledger every portfolio keeps on ms-markets 6.1.0 (its ADR 0065).

It runs here on SQLite through Alembic's own operations, in the batch mode the MetaTables
environment uses for SQLite, next to stand-ins of the two Markets tables its keys reference.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alpaca_connectors.instances import AlpacaPortfolioEventLedgerStorage
from metatables.migrations.sqlite import AUTO_BATCH_OPTION

REVISIONS = (
    Path(__file__).resolve().parents[1]
    / "src/alpaca_connectors/migrations/versions/alpaca_connectors"
)
spec = importlib.util.spec_from_file_location(
    "revision_0037", REVISIONS / "0037_every_portfolio_keeps_a_ledger.py"
)
REVISION = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REVISION)

MARKETS_SCHEMA = (
    "CREATE TABLE ms_markets__asset (unique_identifier VARCHAR(255) PRIMARY KEY)",
    "CREATE TABLE ms_markets__portfolio (unique_identifier VARCHAR(255) PRIMARY KEY)",
)


@pytest.fixture
def connection():
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        for statement in MARKETS_SCHEMA:
            connection.execute(sa.text(statement))
        yield connection


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection, opts={AUTO_BATCH_OPTION: True}))


def test_revision_0037_follows_0036_and_creates_the_ledger_class_table(connection) -> None:
    assert (REVISION.revision, REVISION.down_revision) == ("0037", "0036")
    table = AlpacaPortfolioEventLedgerStorage.__table__
    assert REVISION.LEDGER == table.name
    assert {REVISION.LEDGER_GRAIN_INDEX, REVISION.LEDGER_PORTFOLIO_INDEX} == {
        index.name for index in table.indexes
    }

    with patch.object(REVISION, "op", _operations(connection)):
        REVISION.upgrade()

    inspector = sa.inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns(REVISION.LEDGER)}
    assert set(columns) == {column.name for column in table.columns}
    for column in table.columns:
        assert columns[column.name]["nullable"] is column.nullable, column.name
    assert {index["name"] for index in inspector.get_indexes(REVISION.LEDGER)} == {
        index.name for index in table.indexes
    }
    assert {key["name"] for key in inspector.get_foreign_keys(REVISION.LEDGER)} == {
        key.name for key in table.foreign_key_constraints
    }

    with patch.object(REVISION, "op", _operations(connection)):
        REVISION.downgrade()
    assert REVISION.LEDGER not in sa.inspect(connection).get_table_names()
