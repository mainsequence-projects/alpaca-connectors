"""A portfolio's first rebalance, and what a run and a sync say before it (issue #41)."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from alpaca_connectors.holdings import allocation
from alpaca_connectors.holdings.allocation import size_account_target
from alpaca_connectors.holdings.sizing import FundAllocationError
from alpaca_connectors.portfolios import rebalance_schedule
from alpaca_connectors.portfolios.provided_weights import (
    configuration_uid_from_portfolio_identifier,
    provided_portfolio_unique_identifier,
)
from alpaca_connectors.portfolios.rebalance_schedule import (
    first_rebalance_event,
    first_rebalance_state,
    missing_weights_message,
)

UTC = dt.UTC
# 2026-11-18, the issue's day: weights at 16:07 UTC, the NYSE close at 21:00 UTC.
FIRST_WEIGHTS = dt.datetime(2026, 11, 18, 16, 7, tzinfo=UTC)
CLOSE = dt.datetime(2026, 11, 18, 21, 0, tzinfo=UTC)
CONFIGURATION_UID = uuid.UUID("5c49cb4a-5dbc-4bad-aaad-336dd96b1a1d")


def _rebalance(**values: Any) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            "strategy": "calendar_event_signal",
            "calendar_identifier": "NYSE",
            "session_label": "regular",
            "rebalance_event": "market_close",
            "event_offset_seconds": 0,
            "rebalance_cadence": "every_session",
            "rebalance_weekday": 0,
            **values,
        }
    )


def _session(day: int, *, opens: int = 14, closes: int = 21) -> SimpleNamespace:
    return SimpleNamespace(
        local_date=dt.date(2026, 11, day),
        opens_at=dt.datetime(2026, 11, day, opens, 30, tzinfo=UTC),
        closes_at=dt.datetime(2026, 11, day, closes, 0, tzinfo=UTC),
    )


# Wednesday 18 to Monday 23 November: no sessions on the weekend.
SESSIONS = [_session(17), _session(18), _session(19), _session(20), _session(23)]


def test_the_first_rebalance_is_the_first_close_after_the_first_weights() -> None:
    assert first_rebalance_event(SESSIONS, starts_at=FIRST_WEIGHTS, rebalance=_rebalance()) == CLOSE


def test_the_open_after_the_first_weights_is_the_next_day_s() -> None:
    first = first_rebalance_event(
        SESSIONS, starts_at=FIRST_WEIGHTS, rebalance=_rebalance(rebalance_event="market_open")
    )
    assert first == dt.datetime(2026, 11, 19, 14, 30, tzinfo=UTC)


def test_the_offset_moves_the_event_and_a_weekly_cadence_keeps_its_weekday() -> None:
    offset = _rebalance(event_offset_seconds=-6 * 3600)
    # 21:00 - 6 h = 15:00, before the first weights: the next session's close moved by it.
    assert first_rebalance_event(SESSIONS, starts_at=FIRST_WEIGHTS, rebalance=offset) == (
        dt.datetime(2026, 11, 19, 15, 0, tzinfo=UTC)
    )
    monday = _rebalance(rebalance_cadence="weekly", rebalance_weekday=0)
    assert first_rebalance_event(SESSIONS, starts_at=FIRST_WEIGHTS, rebalance=monday) == (
        dt.datetime(2026, 11, 23, 21, 0, tzinfo=UTC)
    )
    assert first_rebalance_event([], starts_at=FIRST_WEIGHTS, rebalance=_rebalance()) is None


def test_a_run_before_the_first_close_says_when_it_is(monkeypatch) -> None:
    monkeypatch.setattr(rebalance_schedule, "first_rebalance_at", lambda rebalance, **_: CLOSE)

    before = first_rebalance_state(
        _rebalance(), starts_at=FIRST_WEIGHTS, at=dt.datetime(2026, 11, 18, 16, 8, tzinfo=UTC)
    )
    assert before == {
        "rebalanced": False,
        "first_rebalance_at": "2026-11-18T21:00:00+00:00",
        "message": (
            "No rebalance yet: the portfolio's first rebalance is at 2026-11-18T21:00:00+00:00 "
            "(NYSE market_close). It has weights after a run at or after that time."
        ),
    }
    after = first_rebalance_state(
        _rebalance(), starts_at=FIRST_WEIGHTS, at=dt.datetime(2026, 11, 19, 2, tzinfo=UTC)
    )
    assert after == {"rebalanced": True, "first_rebalance_at": "2026-11-18T21:00:00+00:00"}


def test_a_portfolio_identifier_gives_back_its_configuration() -> None:
    identifier = provided_portfolio_unique_identifier(CONFIGURATION_UID)
    assert configuration_uid_from_portfolio_identifier(identifier) == CONFIGURATION_UID
    assert configuration_uid_from_portfolio_identifier("A") is None
    assert configuration_uid_from_portfolio_identifier("ALPACA_PORTFOLIO__NOTHEX") is None


@pytest.fixture
def portfolio(monkeypatch) -> dict[str, Any]:
    from alpaca_connectors.portfolios import configurations, provided_weights, signal_observations

    state: dict[str, Any] = {"first_observation_at": FIRST_WEIGHTS}
    configuration = SimpleNamespace(uid=CONFIGURATION_UID, rebalance_configuration_uid="rb-uid")
    monkeypatch.setattr(
        configurations,
        "get_portfolio_configuration",
        lambda uid: configuration if uid == CONFIGURATION_UID else None,
    )
    monkeypatch.setattr(configurations, "get_rebalance_configuration", lambda uid: _rebalance())
    monkeypatch.setattr(provided_weights, "portfolio_signal_uid", lambda row: "signal-uid")
    monkeypatch.setattr(
        signal_observations,
        "read_signal_observation_bounds",
        lambda *, signal_uid: SimpleNamespace(first_observation_at=state["first_observation_at"]),
    )
    monkeypatch.setattr(rebalance_schedule, "first_rebalance_at", lambda rebalance, **_: CLOSE)
    return state


def test_a_sync_before_the_first_close_says_when_it_is(portfolio) -> None:
    identifier = provided_portfolio_unique_identifier(CONFIGURATION_UID)
    sync = dt.datetime(2026, 11, 18, 16, 10, 26, tzinfo=UTC)

    assert missing_weights_message(identifier, at=sync) == (
        "A portfolio of the account target has no rebalance yet: its first is at "
        "2026-11-18T21:00:00+00:00 (NYSE market_close), after this sync at "
        "2026-11-18T16:10:26+00:00."
    )
    assert missing_weights_message(identifier, at=dt.datetime(2026, 11, 18, 23, tzinfo=UTC)) == (
        "A portfolio of the account target has no weights recorded yet: its first rebalance was "
        "at 2026-11-18T21:00:00+00:00 (NYSE market_close), and no portfolio run has recorded it "
        "since."
    )


def test_a_portfolio_it_cannot_explain_keeps_the_generic_refusal(portfolio) -> None:
    assert missing_weights_message("A", at=CLOSE) is None
    portfolio["first_observation_at"] = None
    identifier = provided_portfolio_unique_identifier(CONFIGURATION_UID)
    assert missing_weights_message(identifier, at=CLOSE) is None


def test_the_sync_refuses_with_the_first_rebalance(monkeypatch, portfolio) -> None:
    from alpaca_connectors import markets_api

    identifier = provided_portfolio_unique_identifier(CONFIGURATION_UID)
    row = {
        "target_type": "portfolio",
        "target_uid": "portfolio-uid",
        "weight_notional_exposure": None,
        "constant_notional_exposure": "1000",
        "portfolio": {"unique_identifier": identifier},
    }
    monkeypatch.setattr(
        markets_api,
        "account_target_positions",
        lambda uid: SimpleNamespace(position_set_uid="set-uid", positions=[row]),
    )
    monkeypatch.setattr(allocation, "read_portfolio_weights", lambda identifiers, *, at: {})

    with pytest.raises(FundAllocationError) as refused:
        size_account_target(
            "account-uid",
            valuation_time=dt.datetime(2026, 11, 18, 16, 10, tzinfo=UTC),
            equity=None,
            price_reader=dict,
        )
    assert refused.value.code == "portfolio_weights_missing"
    assert "its first is at 2026-11-18T21:00:00+00:00 (NYSE market_close)" in str(refused.value)


def test_an_immediate_portfolio_trades_at_the_next_observed_price() -> None:
    """ms-markets ADR 0058: in session from the weights, else from the next open; each asset at
    its next observed price, here the daily bar stamped at the session's close."""
    from alpaca_connectors.portfolios.rebalance_schedule import first_trade_at

    immediate = _rebalance(strategy="immediate_signal")
    # Weights during Wednesday's session: Wednesday's close.
    assert first_trade_at(SESSIONS, starts_at=FIRST_WEIGHTS, rebalance=immediate) == CLOSE
    # Weights on Saturday: Monday's open is the earliest moment, Monday's close the price.
    saturday = dt.datetime(2026, 11, 21, 12, tzinfo=UTC)
    assert first_trade_at(SESSIONS, starts_at=saturday, rebalance=immediate) == (
        dt.datetime(2026, 11, 23, 21, 0, tzinfo=UTC)
    )
    # Weights at the close itself: the price must be observed strictly after them.
    assert first_trade_at(SESSIONS, starts_at=CLOSE, rebalance=immediate) == (
        dt.datetime(2026, 11, 19, 21, 0, tzinfo=UTC)
    )
    assert first_trade_at([], starts_at=FIRST_WEIGHTS, rebalance=immediate) is None


def test_a_calendar_rule_trades_at_the_close_on_or_after_its_event() -> None:
    from alpaca_connectors.portfolios.rebalance_schedule import first_trade_at

    # The close event is in session: its own close bar is the price, as before.
    assert first_trade_at(SESSIONS, starts_at=FIRST_WEIGHTS, rebalance=_rebalance()) == CLOSE
    # The open event is the earliest moment; the price is that session's close bar.
    opens = _rebalance(rebalance_event="market_open")
    assert first_trade_at(SESSIONS, starts_at=FIRST_WEIGHTS, rebalance=opens) == (
        dt.datetime(2026, 11, 19, 21, 0, tzinfo=UTC)
    )


def test_the_first_rebalance_reads_the_stored_calendar_for_both_rules(monkeypatch) -> None:
    from msm.api.calendars import Calendar

    from alpaca_connectors.assets import calendars
    from alpaca_connectors.portfolios.rebalance_schedule import first_rebalance_at

    read = []
    monkeypatch.setattr(
        calendars, "ensure_trading_calendar", lambda identifier: SimpleNamespace(uid="nyse-uid")
    )
    monkeypatch.setattr(
        Calendar,
        "sessions",
        classmethod(lambda cls, uid, start, end, session_label: read.append(uid) or SESSIONS),
    )
    immediate = _rebalance(strategy="immediate_signal")
    assert first_rebalance_at(immediate, starts_at=FIRST_WEIGHTS) == CLOSE
    assert first_rebalance_at(_rebalance(), starts_at=FIRST_WEIGHTS) == CLOSE
    assert read == ["nyse-uid", "nyse-uid"]
    state = first_rebalance_state(
        immediate, starts_at=FIRST_WEIGHTS, at=dt.datetime(2026, 11, 18, 16, 8, tzinfo=UTC)
    )
    assert state["rebalanced"] is False
    assert "(NYSE close after its first weights)" in state["message"]


def test_a_sync_before_an_immediate_portfolio_s_first_run_says_so(monkeypatch, portfolio) -> None:
    from alpaca_connectors.portfolios import configurations

    monkeypatch.setattr(
        configurations,
        "get_rebalance_configuration",
        lambda uid: _rebalance(strategy="immediate_signal"),
    )
    monkeypatch.setattr(
        rebalance_schedule, "first_rebalance_at", lambda rebalance, *, starts_at: starts_at
    )
    identifier = provided_portfolio_unique_identifier(CONFIGURATION_UID)

    assert missing_weights_message(identifier, at=CLOSE) == (
        "A portfolio of the account target has no weights recorded yet: its first rebalance was "
        "at 2026-11-18T16:07:00+00:00 (NYSE close after its first weights), and no portfolio run "
        "has recorded it since."
    )
