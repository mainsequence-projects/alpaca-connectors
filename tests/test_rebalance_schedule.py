"""A portfolio's first rebalance, and what a run and a sync say before it (issues #41, #48)."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from alpaca_connectors.holdings import allocation
from alpaca_connectors.holdings.allocation import size_account_target
from alpaca_connectors.holdings.sizing import FundAllocationError, PortfolioRebalancePending
from alpaca_connectors.portfolios import rebalance_schedule
from alpaca_connectors.portfolios.provided_weights import (
    configuration_uid_from_portfolio_identifier,
    provided_portfolio_unique_identifier,
)
from alpaca_connectors.portfolios.rebalance_schedule import (
    PendingFirstRebalance,
    PortfolioRebalanceTimes,
    first_rebalance_event,
    first_rebalance_state,
    pending_first_rebalance,
    portfolio_rebalance_times,
    run_first_rebalance,
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


def test_a_portfolio_waiting_for_its_first_rebalance_says_when_it_is(portfolio) -> None:
    identifier = provided_portfolio_unique_identifier(CONFIGURATION_UID)

    assert pending_first_rebalance(identifier, portfolio_uid="portfolio-uid") == (
        PendingFirstRebalance(portfolio_uid="portfolio-uid", first_rebalance_at=CLOSE)
    )


def test_a_portfolio_it_cannot_explain_is_not_waiting(portfolio) -> None:
    assert pending_first_rebalance("A", portfolio_uid="portfolio-uid") is None
    portfolio["first_observation_at"] = None
    identifier = provided_portfolio_unique_identifier(CONFIGURATION_UID)
    assert pending_first_rebalance(identifier, portfolio_uid="portfolio-uid") is None


def _target(monkeypatch, *identifiers: str) -> None:
    from alpaca_connectors import markets_api

    rows = [
        {
            "target_type": "portfolio",
            "target_uid": f"portfolio-uid-{position}",
            "weight_notional_exposure": "0.1",
            "portfolio": {"unique_identifier": identifier},
        }
        for position, identifier in enumerate(identifiers)
    ]
    monkeypatch.setattr(
        markets_api,
        "account_target_positions",
        lambda uid: SimpleNamespace(position_set_uid="set-uid", positions=rows),
    )
    monkeypatch.setattr(allocation, "read_portfolio_weights", lambda identifiers, *, at: {})


def _size() -> Any:
    return size_account_target(
        "account-uid",
        valuation_time=dt.datetime(2026, 11, 18, 16, 10, tzinfo=UTC),
        equity=100_000.0,
        price_reader=dict,
    )


def test_a_sync_waits_for_the_first_rebalance_with_its_own_code(monkeypatch, portfolio) -> None:
    """Issue #48: waiting until the first rebalance is not a failure, and says so as fields."""
    _target(monkeypatch, provided_portfolio_unique_identifier(CONFIGURATION_UID))

    with pytest.raises(PortfolioRebalancePending) as waiting:
        _size()
    assert waiting.value.code == "portfolio_rebalance_pending"
    assert waiting.value.portfolios == (
        PendingFirstRebalance(portfolio_uid="portfolio-uid-0", first_rebalance_at=CLOSE),
    )
    # The message names no portfolio.
    assert CONFIGURATION_UID.hex.upper() not in str(waiting.value)


def test_a_portfolio_missing_weights_for_another_reason_still_fails(monkeypatch, portfolio) -> None:
    _target(monkeypatch, provided_portfolio_unique_identifier(CONFIGURATION_UID), "ELSEWHERE")

    with pytest.raises(FundAllocationError) as refused:
        _size()
    assert not isinstance(refused.value, PortfolioRebalancePending)
    assert refused.value.code == "portfolio_weights_missing"


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


def test_an_immediate_portfolio_waits_for_the_close_after_its_first_weights(
    monkeypatch, portfolio
) -> None:
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

    assert pending_first_rebalance(identifier, portfolio_uid="p").first_rebalance_at == (
        FIRST_WEIGHTS
    )


# ------------------------------------------------------------------------------------------------
# The times as fields (issue #48)
# ------------------------------------------------------------------------------------------------
LATER_WEIGHTS = dt.datetime(2026, 11, 21, 14, 11, tzinfo=UTC)
LATER_CLOSE = dt.datetime(2026, 11, 23, 21, 0, tzinfo=UTC)


@pytest.fixture
def calendar(monkeypatch) -> list[dt.datetime]:
    """The calendar as ``first_rebalance_at`` reads it: Wednesday's close for the first weights,
    Monday's for Saturday's; the times it was asked about."""
    asked: list[dt.datetime] = []

    def rebalance_after(rebalance: Any, *, starts_at: dt.datetime) -> dt.datetime:
        asked.append(starts_at)
        return CLOSE if starts_at <= CLOSE else LATER_CLOSE

    monkeypatch.setattr(rebalance_schedule, "first_rebalance_at", rebalance_after)
    return asked


def _bounds(monkeypatch, *, row_count: int = 0, last_time: dt.datetime | None = None) -> list:
    from alpaca_connectors import markets_api

    read: list[str] = []
    monkeypatch.setattr(
        markets_api,
        "portfolio_weights_bounds",
        lambda identifier: (
            read.append(identifier) or SimpleNamespace(row_count=row_count, last_time=last_time)
        ),
    )
    return read


def test_a_new_portfolio_s_times_are_its_first_close_and_nothing_is_recorded(
    monkeypatch, calendar
) -> None:
    read = _bounds(monkeypatch)

    times = portfolio_rebalance_times(
        _rebalance(),
        first_observation_at=FIRST_WEIGHTS,
        last_observation_at=FIRST_WEIGHTS,
        portfolio_identifier=None,
    )

    assert times == PortfolioRebalanceTimes(
        first_rebalance_at=CLOSE,
        first_rebalance_recorded=False,
        weights_effective_at=CLOSE,
        weights_effective_recorded=False,
    )
    # One calendar read for weights that are both the first and the latest, and no Markets read
    # for a portfolio no run has reached.
    assert (calendar, read) == ([FIRST_WEIGHTS], [])


def test_later_weights_take_effect_at_the_rebalance_after_them(monkeypatch, calendar) -> None:
    read = _bounds(monkeypatch, row_count=4, last_time=CLOSE)

    times = portfolio_rebalance_times(
        _rebalance(),
        first_observation_at=FIRST_WEIGHTS,
        last_observation_at=LATER_WEIGHTS,
        portfolio_identifier="ALPACA_PORTFOLIO__X",
    )

    assert times == PortfolioRebalanceTimes(
        first_rebalance_at=CLOSE,
        first_rebalance_recorded=True,
        weights_effective_at=LATER_CLOSE,
        weights_effective_recorded=False,
    )
    assert sorted(calendar) == [FIRST_WEIGHTS, LATER_WEIGHTS]
    assert read == ["ALPACA_PORTFOLIO__X"]

    # Markets holds weights at that rebalance, its bounds' time naive or not.
    _bounds(monkeypatch, row_count=9, last_time=LATER_CLOSE.replace(tzinfo=None))
    recorded = portfolio_rebalance_times(
        _rebalance(),
        first_observation_at=FIRST_WEIGHTS,
        last_observation_at=LATER_WEIGHTS,
        portfolio_identifier="ALPACA_PORTFOLIO__X",
    )
    assert recorded.weights_effective_recorded is True


def test_only_the_first_rebalance_is_read_when_only_it_is_asked(monkeypatch, calendar) -> None:
    _bounds(monkeypatch)

    times = portfolio_rebalance_times(
        _rebalance(),
        first_observation_at=FIRST_WEIGHTS,
        last_observation_at=LATER_WEIGHTS,
        portfolio_identifier="ALPACA_PORTFOLIO__X",
        weights_effective=False,
    )

    assert (times.first_rebalance_at, times.weights_effective_at) == (CLOSE, None)
    assert calendar == [FIRST_WEIGHTS]


def test_a_signal_without_weights_has_no_times(monkeypatch, calendar) -> None:
    _bounds(monkeypatch)

    times = portfolio_rebalance_times(
        _rebalance(),
        first_observation_at=None,
        last_observation_at=None,
        portfolio_identifier="ALPACA_PORTFOLIO__X",
    )

    assert (times.first_rebalance_at, times.weights_effective_at) == (None, None)
    assert calendar == []


def test_a_run_says_what_its_summary_said_from_when_it_started() -> None:
    waiting = PortfolioRebalanceTimes(first_rebalance_at=CLOSE, first_rebalance_recorded=False)
    before = run_first_rebalance(
        _rebalance(), times=waiting, status="SUCCEEDED", started_at="2026-11-18T16:08:00Z"
    )
    assert before == {
        "rebalanced": False,
        "first_rebalance_at": "2026-11-18T21:00:00+00:00",
        "message": (
            "No rebalance yet: the portfolio's first rebalance is at 2026-11-18T21:00:00+00:00 "
            "(NYSE market_close). It has weights after a run at or after that time."
        ),
    }
    after = dt.datetime(2026, 11, 19, 2, tzinfo=UTC)
    # Due, and Markets holds no weights: no run has recorded it.
    unrecorded = run_first_rebalance(
        _rebalance(), times=waiting, status="SUCCEEDED", started_at=after
    )
    assert unrecorded["rebalanced"] is False
    assert "no run has recorded that rebalance" in unrecorded["message"]
    recorded = PortfolioRebalanceTimes(first_rebalance_at=CLOSE, first_rebalance_recorded=True)
    assert run_first_rebalance(
        _rebalance(), times=recorded, status="SUCCEEDED", started_at=after
    ) == {"rebalanced": True, "first_rebalance_at": "2026-11-18T21:00:00+00:00"}
    # A run that did not succeed, or has not started, has none.
    for status, started in (("FAILED", after), ("RUNNING", after), ("QUEUED", None)):
        assert (
            run_first_rebalance(_rebalance(), times=recorded, status=status, started_at=started)
            is None
        )
