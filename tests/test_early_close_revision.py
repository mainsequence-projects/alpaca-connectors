"""Revision 0035 moves the daily bars of NYSE early-close days to their session's close.

It runs here on SQLite through Alembic's own operations, as the MetaTables environment does.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alpaca_connectors.market_data.alpaca_bars_support import stock_sessions
from alpaca_connectors.market_data.storage import ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE
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


REVISION = _revision("0035_stamp_early_close_bars_at_their_close.py")
NEW_YORK_16 = dt.time(16)


def _utc(day: str, clock: str) -> dt.datetime:
    return dt.datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=dt.UTC)


def test_revision_0035_follows_0034_and_moves_both_bars_tables() -> None:
    assert (REVISION.revision, REVISION.down_revision) == ("0035", "0034")
    assert set(REVISION.BARS_TABLES) == {
        storage.__table__.name for storage in ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE.values()
    }


def test_the_early_closes_are_every_nyse_early_close_the_updater_can_have_stored() -> None:
    # From 2018, the first year the updater fetches, through 2026.
    from zoneinfo import ZoneInfo

    new_york = ZoneInfo("America/New_York")
    expected = []
    for day, (_, close) in sorted(
        stock_sessions(dt.date(2018, 1, 1), dt.date(2026, 12, 31)).items()
    ):
        if close.astimezone(new_york).time() == NEW_YORK_16:
            continue
        stamp = dt.datetime.combine(day, NEW_YORK_16, tzinfo=new_york).astimezone(dt.UTC)
        expected.append((day.isoformat(), f"{stamp:%H:%M}", f"{close:%H:%M}"))

    assert list(REVISION.EARLY_CLOSES) == expected


def test_upgrade_moves_only_the_early_close_bars_and_downgrade_moves_them_back() -> None:
    engine = sa.create_engine("sqlite://")
    early, regular = REVISION.EARLY_CLOSES[-1], ("2026-12-23", "21:00", "21:00")
    with engine.begin() as connection:
        for name in REVISION.BARS_TABLES:
            connection.execute(
                sa.text(f"CREATE TABLE {name} (time_index DATETIME, asset_identifier VARCHAR)")
            )
        table = sa.table(
            REVISION.BARS_TABLES[0],
            sa.column("time_index", sa.DateTime(timezone=True)),
            sa.column("asset_identifier", sa.String),
        )
        connection.execute(
            sa.insert(table),
            [
                {"time_index": _utc(early[0], early[1]), "asset_identifier": "A"},
                {"time_index": _utc(regular[0], regular[1]), "asset_identifier": "A"},
            ],
        )

        def stored() -> list[tuple[dt.datetime, str]]:
            rows = connection.execute(
                sa.select(table.c.time_index, table.c.asset_identifier).order_by(table.c.time_index)
            )
            return [(when.replace(tzinfo=dt.UTC), who) for when, who in rows]

        operations = Operations(
            MigrationContext.configure(connection, opts={AUTO_BATCH_OPTION: True})
        )
        with patch.object(REVISION, "op", operations):
            REVISION.upgrade()
            assert stored() == [
                (_utc(regular[0], regular[1]), "A"),
                (_utc(early[0], early[2]), "A"),
            ]
            REVISION.downgrade()
            assert stored() == [
                (_utc(regular[0], regular[1]), "A"),
                (_utc(early[0], early[1]), "A"),
            ]
