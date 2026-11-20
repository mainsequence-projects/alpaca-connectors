"""When a portfolio's first rebalance is (issue #41).

A portfolio has weights only from its first rebalance. With ``immediate_signal`` that is its
signal's first observation. With ``calendar_event_signal`` (NYSE ``market_close`` by default,
ADR 0009) it is the first configured event at or after that observation: a run before that event
succeeds with no weights, and a sync of an account that targets the portfolio finds none. Both
say so, with the time of the first rebalance.

The events are the stored calendar's sessions from ms-markets (``Calendar.sessions``), selected as
``CalendarEventSignal`` selects them: the configured event of the configured session, moved by the
offset, on the configured weekday when the cadence is weekly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import Any

# A configured event follows the first weights within a week for a weekly cadence; three weeks
# also cover a run of holidays.
FIRST_REBALANCE_SEARCH_DAYS = 21


def first_rebalance_event(
    sessions: Iterable[Any],
    *,
    starts_at: dt.datetime,
    rebalance: Any,
) -> dt.datetime | None:
    """The first configured event at or after ``starts_at`` among ``sessions``, or None."""
    event_attribute = "opens_at" if rebalance.rebalance_event == "market_open" else "closes_at"
    offset = dt.timedelta(seconds=int(rebalance.event_offset_seconds))
    events = []
    for session in sessions:
        if rebalance.rebalance_cadence == "weekly" and (
            session.local_date.weekday() != int(rebalance.rebalance_weekday)
        ):
            continue
        event = getattr(session, event_attribute)
        if event is None:
            continue
        at = event.astimezone(dt.UTC) + offset
        if at >= starts_at:
            events.append(at)
    return min(events, default=None)


def first_rebalance_at(rebalance: Any, *, starts_at: dt.datetime) -> dt.datetime | None:
    """The portfolio's first rebalance for a signal first observed at ``starts_at``.

    ``immediate_signal`` trades at the first weights themselves. For ``calendar_event_signal``,
    None when the stored calendar has no configured event in the ``FIRST_REBALANCE_SEARCH_DAYS``
    after them, or does not cover those dates.
    """
    if rebalance.strategy == "immediate_signal":
        return starts_at.astimezone(dt.UTC)
    from msm.api.calendars import Calendar

    from alpaca_connectors.assets.calendars import ensure_trading_calendar

    calendar = ensure_trading_calendar(rebalance.calendar_identifier)
    # A session's local date can be the day before its UTC events.
    first_date = starts_at.astimezone(dt.UTC).date() - dt.timedelta(days=1)
    try:
        sessions = Calendar.sessions(
            calendar.uid,
            first_date,
            first_date + dt.timedelta(days=FIRST_REBALANCE_SEARCH_DAYS),
            session_label=rebalance.session_label,
        )
    except ValueError:
        # ms-markets' CalendarRangeError, a ValueError: the calendar does not cover these dates.
        return None
    return first_rebalance_event(sessions, starts_at=starts_at, rebalance=rebalance)


def describe_event(rebalance: Any) -> str:
    """The configured event in words, as ``NYSE market_close`` or ``its first weights``."""
    if rebalance.strategy == "immediate_signal":
        return "its first weights"
    return f"{rebalance.calendar_identifier} {rebalance.rebalance_event}"


def first_rebalance_state(
    rebalance: Any,
    *,
    starts_at: dt.datetime,
    at: dt.datetime,
) -> dict[str, Any]:
    """Whether a run at ``at`` has reached the portfolio's first rebalance, and when it is."""
    first = first_rebalance_at(rebalance, starts_at=starts_at)
    rebalanced = first is not None and first <= at
    state: dict[str, Any] = {
        "rebalanced": rebalanced,
        "first_rebalance_at": None if first is None else first.isoformat(),
    }
    if rebalanced:
        return state
    if first is None:
        state["message"] = (
            f"No rebalance yet: the calendar has no {describe_event(rebalance)} event in the "
            f"{FIRST_REBALANCE_SEARCH_DAYS} days after the portfolio's first weights."
        )
    else:
        state["message"] = (
            f"No rebalance yet: the portfolio's first rebalance is at {first.isoformat()} "
            f"({describe_event(rebalance)}). It has weights after a run at or after that time."
        )
    return state


def missing_weights_message(portfolio_identifier: str, *, at: dt.datetime) -> str | None:
    """Why a portfolio of this application has no weights at ``at``, or None when unknown.

    For the sync's ``portfolio_weights_missing`` (issue #41): the portfolio's first rebalance is
    after the sync, or it is before it and no run has recorded it yet. None for a portfolio this
    application does not know or whose signal has published nothing.
    """
    from alpaca_connectors.portfolios.configurations import (
        get_portfolio_configuration,
        get_rebalance_configuration,
    )
    from alpaca_connectors.portfolios.provided_weights import (
        configuration_uid_from_portfolio_identifier,
        portfolio_signal_uid,
    )
    from alpaca_connectors.portfolios.signal_observations import read_signal_observation_bounds

    configuration_uid = configuration_uid_from_portfolio_identifier(portfolio_identifier)
    if configuration_uid is None:
        return None
    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        return None
    rebalance = get_rebalance_configuration(configuration.rebalance_configuration_uid)
    if rebalance is None:
        return None
    bounds = read_signal_observation_bounds(signal_uid=portfolio_signal_uid(configuration))
    if bounds.first_observation_at is None:
        return None
    first = first_rebalance_at(rebalance, starts_at=bounds.first_observation_at)
    if first is None:
        return None
    event = describe_event(rebalance)
    if first > at:
        return (
            "A portfolio of the account target has no rebalance yet: its first is at "
            f"{first.isoformat()} ({event}), after this sync at {at.isoformat()}."
        )
    return (
        "A portfolio of the account target has no weights recorded yet: its first rebalance was "
        f"at {first.isoformat()} ({event}), and no portfolio run has recorded it since."
    )


__all__ = [
    "FIRST_REBALANCE_SEARCH_DAYS",
    "describe_event",
    "first_rebalance_at",
    "first_rebalance_event",
    "first_rebalance_state",
    "missing_weights_message",
]
