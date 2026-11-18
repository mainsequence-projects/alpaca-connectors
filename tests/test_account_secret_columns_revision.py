"""The account Secret reference columns go (ADR 0017's transition, issue #13, revision 0030).

The revision runs here on SQLite through Alembic's own operations, in the batch mode the
MetaTables environment uses for SQLite, over the parts of the account table it touches.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alpaca_connectors.account.provider_account_details import ProviderAccountDetails
from metatables.migrations.sqlite import AUTO_BATCH_OPTION

REVISIONS = (
    Path(__file__).resolve().parents[1]
    / "src/alpaca_connectors/migrations/versions/alpaca_connectors"
)
TABLE = "alpaca_connectors__acct_alpaca"
LEGACY = (
    "api_key_secret_name",
    "secret_key_secret_name",
    "api_key_secret_uid",
    "secret_key_secret_uid",
)


def _revision():
    spec = importlib.util.spec_from_file_location(
        "revision_0030", REVISIONS / "0030_drop_account_secret_reference_columns.py"
    )
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


REVISION = _revision()


@pytest.fixture
def connection():
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                f"CREATE TABLE {TABLE} ("
                "account_uid VARCHAR(36) PRIMARY KEY, api_key TEXT, secret_key TEXT, "
                "credential_source VARCHAR(16) NOT NULL DEFAULT 'external', "
                "api_key_secret_name VARCHAR(255), secret_key_secret_name VARCHAR(255), "
                "api_key_secret_uid VARCHAR(64), secret_key_secret_uid VARCHAR(64))"
            )
        )
        for column in ("api_key_secret_name", "secret_key_secret_name"):
            connection.execute(sa.text(f"CREATE INDEX ix__{TABLE}__{column} ON {TABLE} ({column})"))
        connection.execute(
            sa.text(
                f"INSERT INTO {TABLE} (account_uid, api_key, secret_key, credential_source) "
                "VALUES ('stored', 'sealed', 'sealed', 'managed'), ('removed', NULL, NULL, 'imported')"
            )
        )
        yield connection


def _run(connection, step: str) -> None:
    operations = Operations(MigrationContext.configure(connection, opts={AUTO_BATCH_OPTION: True}))
    with patch.object(REVISION, "op", operations):
        getattr(REVISION, step)()


def _columns(connection) -> dict[str, dict]:
    return {column["name"]: column for column in sa.inspect(connection).get_columns(TABLE)}


def test_the_columns_and_the_transition_default_go_and_every_row_stays(connection) -> None:
    _run(connection, "upgrade")

    columns = _columns(connection)
    assert not set(columns) & set(LEGACY)
    assert columns["credential_source"]["default"] is None
    assert sa.inspect(connection).get_indexes(TABLE) == []
    rows = connection.execute(sa.text(f"SELECT account_uid, credential_source FROM {TABLE}"))
    assert sorted(rows) == [("removed", "imported"), ("stored", "managed")]
    # The table now has exactly the model's columns of these.
    assert not set(ProviderAccountDetails.__table__.columns.keys()) & set(LEGACY)


def test_a_registration_never_migrated_refuses_the_revision_and_nothing_changes(connection) -> None:
    connection.execute(
        sa.text(
            f"INSERT INTO {TABLE} (account_uid, api_key_secret_name, secret_key_secret_name) "
            "VALUES ('legacy', 'ALPACA_CONNECTORS__X__API_KEY', 'ALPACA_CONNECTORS__X__SECRET_KEY')"
        )
    )

    with pytest.raises(RuntimeError, match="1 account registration"):
        _run(connection, "upgrade")

    assert set(LEGACY) <= set(_columns(connection))


def test_downgrade_adds_the_columns_back_empty(connection) -> None:
    _run(connection, "upgrade")
    _run(connection, "downgrade")

    columns = _columns(connection)
    assert set(LEGACY) <= set(columns)
    assert all(columns[column]["nullable"] for column in LEGACY)
    assert "external" in str(columns["credential_source"]["default"])
    assert {index["name"] for index in sa.inspect(connection).get_indexes(TABLE)} == {
        f"ix__{TABLE}__api_key_secret_name",
        f"ix__{TABLE}__secret_key_secret_name",
    }
