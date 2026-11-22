"""stamp the daily bars of NYSE early-close days at their session's close

The bars updater stamped every daily bar at 16:00 New York, also on the days the NYSE closes at
13:00. ms-markets puts daily bars on the session closes of the asset's stored calendar, so such a
bar fell between two sessions: interpolation dropped it and valued the day at the previous close,
marked as interpolated. The updater now stamps each bar at its session's close
(``stock_sessions``); this revision moves the bars already stored on those days, in both bars
tables, from 16:00 to the session's close. The open of each of those sessions is 09:30, as stored.

``EARLY_CLOSES`` is every NYSE early close from 2018, the first year the updater fetches, through
2026, from pandas-market-calendars: the same sessions as the stored ``NYSE`` calendar. A day this
application holds no bar for changes nothing. The interpolated-price tables of revision 0034 are
new and empty when this runs, so they hold nothing to move.

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-22 23:00:00.000000

"""

import datetime as dt
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: Union[str, Sequence[str], None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BARS_TABLES = ("alpaca_connectors__bars_1d_sip_all", "alpaca_connectors__bars_1d_iex_raw")

# (session date, the 16:00 New York stamp in UTC, the session's close in UTC)
EARLY_CLOSES = (
    ("2018-07-03", "20:00", "17:00"),
    ("2018-11-23", "21:00", "18:00"),
    ("2018-12-24", "21:00", "18:00"),
    ("2019-07-03", "20:00", "17:00"),
    ("2019-11-29", "21:00", "18:00"),
    ("2019-12-24", "21:00", "18:00"),
    ("2020-11-27", "21:00", "18:00"),
    ("2020-12-24", "21:00", "18:00"),
    ("2021-11-26", "21:00", "18:00"),
    ("2022-11-25", "21:00", "18:00"),
    ("2023-07-03", "20:00", "17:00"),
    ("2023-11-24", "21:00", "18:00"),
    ("2024-07-03", "20:00", "17:00"),
    ("2024-11-29", "21:00", "18:00"),
    ("2024-12-24", "21:00", "18:00"),
    ("2025-07-03", "20:00", "17:00"),
    ("2025-11-28", "21:00", "18:00"),
    ("2025-12-24", "21:00", "18:00"),
    ("2026-11-27", "21:00", "18:00"),
    ("2026-12-24", "21:00", "18:00"),
)


def _utc(day: str, clock: str) -> dt.datetime:
    return dt.datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=dt.UTC)


def _move(source: int, target: int) -> None:
    for name in BARS_TABLES:
        table = sa.table(name, sa.column("time_index", sa.DateTime(timezone=True)))
        for early_close in EARLY_CLOSES:
            day = early_close[0]
            op.execute(
                sa.update(table)
                .where(table.c.time_index == _utc(day, early_close[source]))
                .values(time_index=_utc(day, early_close[target]))
            )


def upgrade() -> None:
    """Move each early-close day's bars from the 16:00 stamp to the session's close."""
    _move(source=1, target=2)


def downgrade() -> None:
    """Move them back to 16:00, as the previous release stamps them."""
    _move(source=2, target=1)
