"""A portfolio run skips its bars update when every asset already holds its last final bar.

The bars update requests only finished periods: a daily bar starts at its session's midnight in New
York, is kept once that start is before today's 00:00 UTC, and is stamped at the session's close.
``last_final_daily_close`` is that bar's stamp; ``daily_bars_current_through`` compares each
asset's latest stored bar with it in one read.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from alpaca_connectors.market_data import services
from alpaca_connectors.market_data.services import (
    daily_bars_current_through,
    last_final_daily_close,
    last_session_close_before,
)


def _utc(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("now", "close"),
    [
        # Monday evening: Monday's bar is not final until Tuesday 00:00 UTC; Friday's is.
        ("2026-11-23T20:10:00", "2026-11-20T21:00:00"),
        # Tuesday 03:00 UTC: Monday's session started before today's 00:00 UTC.
        ("2026-11-24T03:00:00", "2026-11-23T21:00:00"),
        # Thanksgiving is closed and the next day closes at 13:00 New York.
        ("2026-11-28T12:00:00", "2026-11-27T18:00:00"),
        ("2026-11-30T01:00:00", "2026-11-27T18:00:00"),
    ],
)
def test_the_last_final_bar_is_the_last_session_started_before_today_utc(now, close) -> None:
    assert last_final_daily_close(_utc(now)) == _utc(close)


def _check(latest: dict[str, str], identifiers, *, profile=("1d", "sip", "all")):
    rows = [
        {"asset_identifier": identifier, "latest_observation": time}
        for identifier, time in latest.items()
    ]
    runtime = SimpleNamespace(context=object())
    with (
        patch("alpaca_connectors.runtime.start_markets_engine", return_value=runtime),
        patch("msm.repositories.base.compile_markets_statement") as compile_statement,
        patch("msm.repositories.base.execute_markets_operation", return_value=object()),
        patch.object(services, "operation_result_rows", return_value=rows),
    ):
        answer = daily_bars_current_through(profile, identifiers, now=_utc("2026-11-23T20:10:00"))
    return answer, compile_statement


def test_assets_holding_the_last_final_bar_need_no_update() -> None:
    answer, compile_statement = _check(
        {"A": "2026-11-20T21:00:00+00:00", "B": "2026-11-20T21:00:00+00:00"}, ["B", "A"]
    )
    assert answer == _utc("2026-11-20T21:00:00")
    # One read of the profile's table, for the run's assets only.
    [call] = compile_statement.call_args_list
    assert "IN" in str(call.args[0]) and "max" in str(call.args[0]).lower()


def test_an_asset_behind_or_without_bars_runs_the_update() -> None:
    assert (
        _check({"A": "2026-11-20T21:00:00+00:00", "B": "2026-11-19T21:00:00+00:00"}, ["A", "B"])[0]
        is None
    )
    assert _check({"A": "2026-11-20T21:00:00+00:00"}, ["A", "NEW"])[0] is None


def test_only_daily_bars_are_checked() -> None:
    answer, compile_statement = _check({}, ["A"], profile=("1h", "sip", "all"))
    assert answer is None
    compile_statement.assert_not_called()


@pytest.mark.parametrize(
    ("moment", "close"),
    [
        # An hour into Thursday's session: Wednesday's close, the last price before it.
        ("2026-11-19T15:30:00", "2026-11-18T21:00:00"),
        # Monday morning: Friday's close.
        ("2026-11-23T15:30:00", "2026-11-20T21:00:00"),
        # After Monday's close: Monday's close.
        ("2026-11-23T21:30:00", "2026-11-23T21:00:00"),
        # Exactly at a close: the session before, since a ledger opens strictly before its signal.
        ("2026-11-23T21:00:00", "2026-11-20T21:00:00"),
        # The Monday after Thanksgiving: Friday's early close at 13:00 New York.
        ("2026-11-30T15:30:00", "2026-11-27T18:00:00"),
    ],
)
def test_a_ledger_opens_at_the_last_close_before_its_signal(moment, close) -> None:
    assert last_session_close_before(_utc(moment)) == _utc(close)
