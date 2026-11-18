"""Accounts own portfolios, signals and bars (ADR 0028, revision 0029).

The revision runs here on SQLite through Alembic's own operations, in the batch mode the
MetaTables environment uses for SQLite, over the tables as revision 0028 left them (the parts it
reads), with foreign keys enforced.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alpaca_connectors.market_data.configurations import AlpacaBarsConfigurationTable
from alpaca_connectors.portfolios.configurations import (
    AlpacaPortfolioConfigurationTable,
    EndedPortfolioTable,
)
from alpaca_connectors.portfolios.signal_definitions import SignalDefinitionTable
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


REVISION = _revision("0029_add_account_owned_portfolios.py")
OWNER, OTHER = ("gateway-1", "person-1"), ("gateway-2", "person-2")
ACCOUNT_A, ACCOUNT_B, UNOWNED = (uuid.UUID(int=n) for n in (101, 102, 103))
REBALANCE = uuid.UUID(int=9)


def _previous_schema(connection: sa.Connection) -> None:
    """The tables revision 0029 reads, as revision 0028 left them (the parts it uses)."""
    for statement in (
        "CREATE TABLE ms_markets__account (uid CHAR(32) PRIMARY KEY)",
        "CREATE TABLE ms_markets__portfolio (uid CHAR(32) PRIMARY KEY)",
        "CREATE TABLE alpaca_connectors__acct_alpaca (account_uid CHAR(32) PRIMARY KEY, "
        "owner_gateway_uid VARCHAR(64), owner_user_uid VARCHAR(255))",
        "CREATE TABLE alpaca_connectors__portfolio_rebalance_configuration "
        "(uid CHAR(32) PRIMARY KEY)",
        "CREATE TABLE alpaca_connectors__bars_configuration (uid CHAR(32) NOT NULL, "
        "name VARCHAR(255) NOT NULL, description TEXT, enabled BOOLEAN NOT NULL, "
        "account_uid CHAR(32) NOT NULL, asset_source VARCHAR(32) NOT NULL, "
        "frequency_id VARCHAR(16) NOT NULL, feed VARCHAR(16) NOT NULL, "
        "adjustment VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL, scheduled BOOLEAN NOT NULL, "
        "CONSTRAINT pk__alpaca_connectors__bars_configuration PRIMARY KEY (uid), "
        f"CONSTRAINT {REVISION.BARS_ACCOUNT_KEY} FOREIGN KEY(account_uid) "
        "REFERENCES ms_markets__account (uid) ON DELETE RESTRICT)",
        "CREATE TABLE alpaca_connectors__portfolio_configuration (uid CHAR(32) NOT NULL, "
        "name VARCHAR(255) NOT NULL, description TEXT, bars_configuration_uid CHAR(32) NOT NULL, "
        "rebalance_configuration_uid CHAR(32) NOT NULL, portfolio_uid CHAR(32), "
        "upsample_frequency_id VARCHAR(16) NOT NULL, "
        "intraday_bar_interpolation_rule VARCHAR(16) NOT NULL, "
        "valuation_column VARCHAR(64) NOT NULL, valuation_maximum_staleness_seconds INTEGER "
        "NOT NULL, fail_on_missing_prices BOOLEAN NOT NULL, commission_fee FLOAT NOT NULL, "
        "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, scheduled BOOLEAN NOT NULL, "
        "owner_gateway_uid VARCHAR(64) NOT NULL, owner_user_uid VARCHAR(255) NOT NULL, "
        "CONSTRAINT pk__alpaca_connectors__portfolio_configuration PRIMARY KEY (uid), "
        f"CONSTRAINT {REVISION.PORTFOLIO_BARS_KEY} FOREIGN KEY(bars_configuration_uid) "
        "REFERENCES alpaca_connectors__bars_configuration (uid) ON DELETE RESTRICT, "
        "CONSTRAINT fk__alpaca_connectors__portfolio_configuration__reba_19271ffaa5 "
        "FOREIGN KEY(rebalance_configuration_uid) "
        "REFERENCES alpaca_connectors__portfolio_rebalance_configuration (uid) "
        "ON DELETE RESTRICT)",
    ):
        connection.exec_driver_sql(statement)
    for account in (ACCOUNT_A, ACCOUNT_B, UNOWNED):
        connection.exec_driver_sql(f"INSERT INTO ms_markets__account VALUES ('{account.hex}')")
    for account, owner in ((ACCOUNT_A, OWNER), (ACCOUNT_B, OTHER), (UNOWNED, (None, None))):
        connection.execute(
            sa.text(
                "INSERT INTO alpaca_connectors__acct_alpaca VALUES (:account, :gateway, :user)"
            ),
            {"account": account.hex, "gateway": owner[0], "user": owner[1]},
        )
    connection.exec_driver_sql(
        "INSERT INTO alpaca_connectors__portfolio_rebalance_configuration "
        f"VALUES ('{REBALANCE.hex}')"
    )


def _bars(connection, uid: uuid.UUID, account: uuid.UUID, source: str, **values) -> None:
    row = {
        "uid": uid.hex,
        "name": f"Bars {uid.int}",
        "description": f"Bars {uid.int} description",
        "enabled": True,
        "account_uid": account.hex,
        "asset_source": source,
        "frequency_id": "1d",
        "feed": "sip",
        "adjustment": "all",
        "created_at": "2026-11-16 09:00:00",
        "updated_at": "2026-11-16 09:00:00",
        "scheduled": True,
        **values,
    }
    columns = ", ".join(row)
    connection.execute(
        sa.text(
            f"INSERT INTO alpaca_connectors__bars_configuration ({columns}) "
            f"VALUES ({', '.join(':' + name for name in row)})"
        ),
        row,
    )


def _portfolio(connection, uid: uuid.UUID, bars: uuid.UUID, owner=OWNER) -> None:
    row = {
        "uid": uid.hex,
        "name": f"Portfolio {uid.int}",
        "description": None,
        "bars_configuration_uid": bars.hex,
        "rebalance_configuration_uid": REBALANCE.hex,
        "portfolio_uid": None,
        "upsample_frequency_id": "1d",
        "intraday_bar_interpolation_rule": "ffill",
        "valuation_column": "close",
        "valuation_maximum_staleness_seconds": 86_400,
        "fail_on_missing_prices": True,
        "commission_fee": 0.00018,
        "created_at": "2026-11-16 09:00:00",
        "updated_at": "2026-11-16 09:00:00",
        "scheduled": True,
        "owner_gateway_uid": owner[0],
        "owner_user_uid": owner[1],
    }
    columns = ", ".join(row)
    connection.execute(
        sa.text(
            f"INSERT INTO alpaca_connectors__portfolio_configuration ({columns}) "
            f"VALUES ({', '.join(':' + name for name in row)})"
        ),
        row,
    )


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection, opts={AUTO_BATCH_OPTION: True}))


def _upgrade(connection: sa.Connection) -> None:
    with patch.object(REVISION, "op", _operations(connection)):
        REVISION.upgrade()


def _downgrade(connection: sa.Connection) -> None:
    with patch.object(REVISION, "op", _operations(connection)):
        REVISION.downgrade()


def _rows(connection, query: str, **params) -> list[sa.RowMapping]:
    return list(connection.execute(sa.text(query), params).mappings())


@pytest.fixture
def connection():
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        _previous_schema(connection)
        yield connection


def _enforce_keys(connection: sa.Connection) -> None:
    """Every key holds after the revision, and holds from now on.

    MetaTables migrates SQLite with keys off and checks them all afterwards; the pragma applies
    outside a transaction only.
    """
    connection.commit()
    assert list(connection.exec_driver_sql("PRAGMA foreign_key_check")) == []
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


# P1 names its own `portfolios` feed; P3 and P4 share one; P2 runs on another owner's account;
# P5 on an account without an owner. F4 is an account feed nobody names.
P1, P2, P3, P4, P5 = (uuid.UUID(int=n) for n in (1, 2, 3, 4, 5))
F1, F2, F3, F4, F5 = (uuid.UUID(int=n) for n in (11, 12, 13, 14, 15))


def _fleet(connection) -> None:
    _bars(connection, F1, ACCOUNT_A, "portfolios", scheduled=False)
    _bars(connection, F2, ACCOUNT_B, "assets")
    _bars(connection, F3, ACCOUNT_A, "portfolios", feed="iex", adjustment="raw")
    _bars(connection, F4, ACCOUNT_A, "assets")
    _bars(connection, F5, UNOWNED, "portfolios")
    _portfolio(connection, P1, F1)
    _portfolio(connection, P2, F2)
    _portfolio(connection, P3, F3)
    _portfolio(connection, P4, F3)
    _portfolio(connection, P5, F5)


def test_adoption_gives_each_portfolio_its_account_signal_and_own_bars(connection) -> None:
    _fleet(connection)
    _upgrade(connection)
    _enforce_keys(connection)

    portfolios = {
        row["uid"]: row
        for row in _rows(connection, "SELECT * FROM alpaca_connectors__portfolio_configuration")
    }
    for uid in (P1, P3, P4):
        row = portfolios[uid.hex]
        assert row["account_uid"] == ACCOUNT_A.hex
        assert row["bars_configuration_uid"] is None
        (definition,) = _rows(
            connection,
            "SELECT * FROM alpaca_connectors__signal_definition WHERE uid = :uid",
            uid=row["signal_definition_uid"],
        )
        # The portfolio's signal continues: the key is still its configuration UID.
        assert definition["account_uid"] == ACCOUNT_A.hex
        assert definition["key"] == definition["signal_key"] == str(uid)
        (own,) = _rows(
            connection,
            "SELECT * FROM alpaca_connectors__bars_configuration "
            "WHERE portfolio_configuration_uid = :uid",
            uid=uid.hex,
        )
        assert own["account_uid"] is None
        assert own["asset_source"] == "portfolios"
    own_bars = {
        row["portfolio_configuration_uid"]: row
        for row in _rows(
            connection,
            "SELECT * FROM alpaca_connectors__bars_configuration "
            "WHERE portfolio_configuration_uid IS NOT NULL",
        )
    }
    assert own_bars[P1.hex]["scheduled"] in (False, 0)
    assert (own_bars[P3.hex]["feed"], own_bars[P3.hex]["adjustment"]) == ("iex", "raw")
    assert own_bars[P3.hex]["uid"] != own_bars[P4.hex]["uid"]
    # Unadopted: another owner's account, and an account without an owner.
    for uid, bars in ((P2, F2), (P5, F5)):
        assert portfolios[uid.hex]["account_uid"] is None
        assert portfolios[uid.hex]["signal_definition_uid"] is None
        assert portfolios[uid.hex]["bars_configuration_uid"] == bars.hex
    # `portfolios` feeds nobody names are gone; the rest stay as their account's feeds.
    feeds = {
        row["uid"]
        for row in _rows(
            connection,
            "SELECT uid FROM alpaca_connectors__bars_configuration "
            "WHERE portfolio_configuration_uid IS NULL",
        )
    }
    assert feeds == {F2.hex, F4.hex, F5.hex}


def test_tables_match_the_models_after_the_revision(connection) -> None:
    _upgrade(connection)
    inspector = sa.inspect(connection)
    for model in (
        SignalDefinitionTable,
        EndedPortfolioTable,
        AlpacaPortfolioConfigurationTable,
        AlpacaBarsConfigurationTable,
    ):
        table = model.__table__
        created = {column["name"]: column for column in inspector.get_columns(table.name)}
        # The previous schema here carries only the columns the revision reads.
        for column in table.columns:
            if column.name in created:
                assert created[column.name]["nullable"] == column.nullable, (
                    table.name,
                    column.name,
                )
    for model, names in (
        (SignalDefinitionTable, {REVISION.DEFINITION_ACCOUNT_KEY}),
        (
            AlpacaPortfolioConfigurationTable,
            {REVISION.PORTFOLIO_ACCOUNT_KEY, REVISION.PORTFOLIO_DEFINITION_KEY},
        ),
        (AlpacaBarsConfigurationTable, {REVISION.BARS_ACCOUNT_KEY, REVISION.BARS_PORTFOLIO_KEY}),
    ):
        declared = {key.name for key in model.__table__.foreign_key_constraints}
        assert names <= declared
        assert names <= {key["name"] for key in inspector.get_foreign_keys(model.__table__.name)}
    assert REVISION.PORTFOLIO_BARS_KEY not in {
        key["name"] for key in inspector.get_foreign_keys(REVISION.PORTFOLIO_TABLE)
    }
    declared_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in AlpacaBarsConfigurationTable.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert declared_checks[REVISION.BARS_ONE_PARENT[0]] == REVISION.BARS_ONE_PARENT[1]
    declared_indexes = {
        index.name: ([c.name for c in index.columns], index.unique)
        for model in (SignalDefinitionTable, EndedPortfolioTable)
        for index in model.__table__.indexes
    }
    assert declared_indexes == {
        name: (columns, unique)
        for name, columns, unique in (*REVISION.DEFINITION_INDEXES, *REVISION.ENDED_INDEXES)
    }


def test_a_bars_configuration_has_exactly_one_parent(connection) -> None:
    _fleet(connection)
    _upgrade(connection)
    _enforce_keys(connection)
    with pytest.raises(sa.exc.IntegrityError):
        connection.exec_driver_sql(
            "UPDATE alpaca_connectors__bars_configuration SET account_uid = NULL "
            f"WHERE uid = '{F4.hex}'"
        )
    with pytest.raises(sa.exc.IntegrityError):
        connection.exec_driver_sql(
            "UPDATE alpaca_connectors__bars_configuration "
            f"SET portfolio_configuration_uid = '{P1.hex}' WHERE uid = '{F4.hex}'"
        )


def test_a_portfolio_follows_only_a_definition_of_its_own_account(connection) -> None:
    _fleet(connection)
    _upgrade(connection)
    _enforce_keys(connection)
    definition = _rows(
        connection,
        "SELECT signal_definition_uid FROM alpaca_connectors__portfolio_configuration "
        "WHERE uid = :uid",
        uid=P1.hex,
    )[0]["signal_definition_uid"]
    with pytest.raises(sa.exc.IntegrityError):
        connection.exec_driver_sql(
            "UPDATE alpaca_connectors__portfolio_configuration "
            f"SET account_uid = '{ACCOUNT_B.hex}', signal_definition_uid = '{definition}' "
            f"WHERE uid = '{P2.hex}'"
        )


def test_downgrade_puts_every_portfolio_back_on_an_account_feed(connection) -> None:
    _fleet(connection)
    _upgrade(connection)
    own = {
        row["portfolio_configuration_uid"]: row["uid"]
        for row in _rows(
            connection,
            "SELECT uid, portfolio_configuration_uid FROM alpaca_connectors__bars_configuration "
            "WHERE portfolio_configuration_uid IS NOT NULL",
        )
    }

    _downgrade(connection)

    tables = set(sa.inspect(connection).get_table_names())
    assert tables.isdisjoint({REVISION.DEFINITION_TABLE, REVISION.ENDED_TABLE})
    portfolios = {
        row["uid"]: row["bars_configuration_uid"]
        for row in _rows(connection, "SELECT * FROM alpaca_connectors__portfolio_configuration")
    }
    assert portfolios[P1.hex] == own[P1.hex]
    assert portfolios[P2.hex] == F2.hex
    (feed,) = _rows(
        connection,
        "SELECT * FROM alpaca_connectors__bars_configuration WHERE uid = :uid",
        uid=own[P1.hex],
    )
    assert feed["account_uid"] == ACCOUNT_A.hex
    assert "portfolio_configuration_uid" not in feed
