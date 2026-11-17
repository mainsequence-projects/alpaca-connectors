"""Every portfolio moves to the one configuration table (ADR 0027, revision 0027).

Each revision runs here on SQLite through Alembic's own operations, over the tables it reads as
the revision before it left them.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alpaca_connectors.portfolios.configurations import AlpacaPortfolioConfigurationTable

REVISIONS = (
    Path(__file__).resolve().parents[1]
    / "src/alpaca_connectors/migrations/versions/alpaca_connectors"
)
OWNER = "owner-uid"
PORTFOLIO = uuid.UUID(int=7)
BARS = uuid.UUID(int=8)
REBALANCE = uuid.UUID(int=9)


def _revision(file_name: str):
    spec = importlib.util.spec_from_file_location(
        f"revision_{file_name[:4]}", REVISIONS / file_name
    )
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


REVISION_0025 = _revision("0025_add_provided_weights_portfolios.py")
REVISION_0027 = _revision("0027_add_portfolio_configurations.py")
# The names the previous release's tables and rows used, read from the revisions that made them.
PREVIOUS_TABLE = REVISION_0027.PREVIOUS_TABLE
PREVIOUS_SIGNAL_TABLE = PREVIOUS_TABLE.replace("portfolio", "signal_job")
PREVIOUS_WEIGHTS_SOURCE = next(
    value
    for value in re.findall(r"'(\w+)'", REVISION_0025.PORTFOLIO_CHECKS[0][1])
    if value != "provided"
)


def _previous_schema(connection: sa.Connection) -> None:
    """The tables revision 0027 reads, as revision 0026 left them (the columns it uses)."""
    for statement in (
        "CREATE TABLE ms_markets__portfolio (uid CHAR(32) PRIMARY KEY)",
        "CREATE TABLE alpaca_connectors__portfolio_rebalance_configuration "
        "(uid CHAR(32) PRIMARY KEY)",
        "CREATE TABLE alpaca_connectors__universe_source (uid CHAR(32) PRIMARY KEY)",
        "CREATE TABLE alpaca_connectors__asset_universe (uid CHAR(32) PRIMARY KEY)",
        "CREATE TABLE alpaca_connectors__bars_configuration (uid CHAR(32) PRIMARY KEY, "
        "asset_source VARCHAR(32) NOT NULL, universe_uid CHAR(32))",
        f"CREATE TABLE {PREVIOUS_SIGNAL_TABLE} (uid CHAR(32) PRIMARY KEY)",
        f"CREATE TABLE {PREVIOUS_TABLE} (uid CHAR(32) PRIMARY KEY, "
        "name VARCHAR(255) NOT NULL, description TEXT, signal_configuration_uid CHAR(32), "
        "bars_configuration_uid CHAR(32) NOT NULL, rebalance_configuration_uid CHAR(32) NOT NULL, "
        "portfolio_uid CHAR(32), job_uid CHAR(32), upsample_frequency_id VARCHAR(16) NOT NULL, "
        "intraday_bar_interpolation_rule VARCHAR(16) NOT NULL, "
        "valuation_column VARCHAR(64) NOT NULL, fail_on_missing_prices BOOLEAN NOT NULL, "
        "commission_fee FLOAT NOT NULL, created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL, valuation_maximum_staleness_seconds INTEGER NOT NULL, "
        "scheduled BOOLEAN NOT NULL, weights_source VARCHAR(16) NOT NULL, "
        "owner_gateway_uid VARCHAR(64), owner_user_uid VARCHAR(255))",
    ):
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql(f"INSERT INTO ms_markets__portfolio VALUES ('{PORTFOLIO.hex}')")
    connection.exec_driver_sql(
        f"INSERT INTO alpaca_connectors__portfolio_rebalance_configuration VALUES ('{REBALANCE.hex}')"
    )
    connection.exec_driver_sql(
        f"INSERT INTO alpaca_connectors__bars_configuration VALUES ('{BARS.hex}', 'portfolios', NULL)"
    )


def _configuration_row(connection: sa.Connection, uid: uuid.UUID, **values) -> None:
    row = {
        "uid": uid.hex,
        "name": f"Portfolio {uid.int}",
        "description": None,
        "signal_configuration_uid": None,
        "bars_configuration_uid": BARS.hex,
        "rebalance_configuration_uid": REBALANCE.hex,
        "portfolio_uid": None,
        "job_uid": None,
        "upsample_frequency_id": "1d",
        "intraday_bar_interpolation_rule": "ffill",
        "valuation_column": "close",
        "fail_on_missing_prices": True,
        "commission_fee": 0.00018,
        "created_at": "2026-11-16 09:00:00",
        "updated_at": "2026-11-16 09:00:00",
        "valuation_maximum_staleness_seconds": 86_400,
        "scheduled": True,
        "weights_source": "provided",
        "owner_gateway_uid": OWNER,
        "owner_user_uid": OWNER,
        **values,
    }
    columns = ", ".join(row)
    placeholders = ", ".join(f":{name}" for name in row)
    connection.execute(
        sa.text(f"INSERT INTO {PREVIOUS_TABLE} ({columns}) VALUES ({placeholders})"),
        row,
    )


def _upgrade(connection: sa.Connection, revision) -> None:
    with patch.object(revision, "op", Operations(MigrationContext.configure(connection))):
        revision.upgrade()


@pytest.fixture
def connection():
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        _previous_schema(connection)
        yield connection


def test_revision_0027_copies_every_portfolio_under_its_own_uid(connection) -> None:
    first, second = uuid.UUID(int=1), uuid.UUID(int=2)
    _configuration_row(connection, first, portfolio_uid=PORTFOLIO.hex, scheduled=False)
    _configuration_row(connection, second, owner_gateway_uid="gateway", owner_user_uid="person")

    _upgrade(connection, REVISION_0027)

    rows = {
        row["uid"]: row
        for row in connection.execute(
            sa.text(f"SELECT * FROM {REVISION_0027.TABLE_NAME}")
        ).mappings()
    }
    assert set(rows) == {first.hex, second.hex}
    assert rows[first.hex]["portfolio_uid"] == PORTFOLIO.hex
    assert rows[first.hex]["scheduled"] == 0
    assert (rows[second.hex]["owner_gateway_uid"], rows[second.hex]["owner_user_uid"]) == (
        "gateway",
        "person",
    )
    assert set(rows[first.hex]).isdisjoint(
        {"weights_source", "signal_configuration_uid", "job_uid"}
    )
    # The previous table stays until revision 0028, for the release still serving.
    previous = connection.execute(sa.text(f"SELECT COUNT(*) FROM {PREVIOUS_TABLE}")).scalar()
    assert previous == 2


@pytest.mark.parametrize(
    ("remaining", "named"),
    [
        (
            lambda connection: _configuration_row(
                connection,
                uuid.UUID(int=3),
                weights_source=PREVIOUS_WEIGHTS_SOURCE,
                signal_configuration_uid=uuid.UUID(int=4).hex,
                owner_gateway_uid=None,
                owner_user_uid=None,
            ),
            f"1 {REVISION_0027.REMAINING_OBJECTS[0][0]}",
        ),
        (
            lambda connection: connection.exec_driver_sql(
                f"INSERT INTO {PREVIOUS_SIGNAL_TABLE} VALUES ('{uuid.UUID(int=4).hex}')"
            ),
            f"1 {REVISION_0027.REMAINING_OBJECTS[1][0]}",
        ),
        (
            lambda connection: connection.exec_driver_sql(
                "INSERT INTO alpaca_connectors__bars_configuration "
                f"VALUES ('{uuid.UUID(int=5).hex}', 'universe', '{uuid.UUID(int=6).hex}')"
            ),
            f"1 {REVISION_0027.REMAINING_OBJECTS[2][0]}",
        ),
        (
            lambda connection: connection.exec_driver_sql(
                f"INSERT INTO alpaca_connectors__asset_universe VALUES ('{uuid.UUID(int=6).hex}')"
            ),
            f"1 {REVISION_0027.REMAINING_OBJECTS[3][0]}",
        ),
        (
            lambda connection: connection.exec_driver_sql(
                f"INSERT INTO alpaca_connectors__universe_source VALUES ('{uuid.UUID(int=6).hex}')"
            ),
            f"1 {REVISION_0027.REMAINING_OBJECTS[4][0]}",
        ),
    ],
)
def test_revision_0027_refuses_until_the_previous_release_drained_everything(
    connection, remaining, named
) -> None:
    _configuration_row(connection, uuid.UUID(int=1))
    remaining(connection)

    with pytest.raises(RuntimeError, match=named):
        _upgrade(connection, REVISION_0027)

    assert REVISION_0027.TABLE_NAME not in sa.inspect(connection).get_table_names()


def test_revision_0027_creates_the_table_the_model_declares(connection) -> None:
    _upgrade(connection, REVISION_0027)
    table = AlpacaPortfolioConfigurationTable.__table__
    inspector = sa.inspect(connection)

    assert REVISION_0027.TABLE_NAME == table.name
    created = {column["name"]: column for column in inspector.get_columns(table.name)}
    assert set(created) == set(table.columns.keys())
    for column in table.columns:
        assert created[column.name]["nullable"] == column.nullable, column.name
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert dict(REVISION_0027.CHECKS) == checks
    indexes = {
        index.name: ([c.name for c in index.columns], index.unique) for index in table.indexes
    }
    assert {name: (columns, unique) for name, columns, unique in REVISION_0027.INDEXES} == indexes
    foreign_keys = {
        constraint.name: constraint.referred_table.name
        for constraint in table.foreign_key_constraints
    }
    assert {
        key["name"]: key["referred_table"] for key in inspector.get_foreign_keys(table.name)
    } == (foreign_keys)
