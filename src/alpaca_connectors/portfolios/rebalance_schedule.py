"""When a portfolio's first rebalance is (issue #41), and when its latest weights take effect.

A portfolio has weights only from its first rebalance: a run before it succeeds with no weights,
and a sync of an account that targets the portfolio finds none. Both say so, with its time. The
same rule says when every later set of weights takes effect; until then the portfolio holds the
previous ones. Callers read both times, and whether Markets holds them, as fields (issue #48):
the configuration's detail, the provided-weights create, the weights route, the run history and a
sync whose allocation waits for a first rebalance.

A trade happens at the next observed price (ms-markets ADR 0058). The strategy's moment is the
signal's first observation for ``immediate_signal``, and the first configured event at or after
it for ``calendar_event_signal`` (NYSE ``market_close`` by default, ADR 0009). The earliest moment
is that moment while the calendar is in session, otherwise the next open; each asset then trades
at its first price observed at or after it, strictly after the signal, inside a session. This
application's prices are daily Alpaca bars, stamped at the session's close, so the first
rebalance is the first session close at or after the earliest moment and after the signal.

The events are the stored calendar's sessions from ms-markets (``Calendar.sessions``), selected as
``CalendarEventSignal`` selects them: the configured event of the configured session, moved by the
offset, on the configured weekday when the cadence is weekly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# A configured event follows the first weights within a week for a weekly cadence; three weeks
# also cover a run of holidays.
FIRST_REBALANCE_SEARCH_DAYS = 21


@dataclass(frozen=True, slots=True)
class PortfolioRebalanceTimes:
    """When a portfolio's weights take effect, and whether Markets holds them (issue #48).

    ``first_rebalance_at`` is the first rebalance, after the first weights; ``weights_effective_at``
    is when the latest weights given take effect. Either is None when the calendar has no
    rebalance in the ``FIRST_REBALANCE_SEARCH_DAYS`` after those weights. ``first_rebalance_recorded``
    is true once Markets holds any weights of the portfolio, as a run checks it;
    ``weights_effective_recorded`` once it holds weights at or after ``weights_effective_at``.
    """

    first_rebalance_at: dt.datetime | None
    first_rebalance_recorded: bool
    weights_effective_at: dt.datetime | None = None
    weights_effective_recorded: bool = False


@dataclass(frozen=True, slots=True)
class PendingFirstRebalance:
    """A portfolio of an account target that has no weights only because it has not had its first
    rebalance (issue #48): it is after the sync, or no run has recorded it yet.

    ``portfolio_uid`` is the ms-markets ``Portfolio`` the target names. ``recorded`` is false: a
    recorded first rebalance gives the portfolio weights.
    """

    portfolio_uid: str
    first_rebalance_at: dt.datetime
    recorded: bool = False


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


def first_trade_at(
    sessions: Iterable[Any],
    *,
    starts_at: dt.datetime,
    rebalance: Any,
) -> dt.datetime | None:
    """The first rebalance among ``sessions`` for a signal first observed at ``starts_at``.

    The strategy's moment (the first weights, or the first configured event after them), moved to
    the next open when the market is closed then, and filled at the first session close at or after
    it and after the weights: the daily bar observed then. None when ``sessions`` hold none.
    """
    starts_at = starts_at.astimezone(dt.UTC)
    bounds = sorted(
        (session.opens_at.astimezone(dt.UTC), session.closes_at.astimezone(dt.UTC))
        for session in sessions
        if session.opens_at is not None and session.closes_at is not None
    )
    if rebalance.strategy == "immediate_signal":
        moment: dt.datetime | None = starts_at
    else:
        moment = first_rebalance_event(sessions, starts_at=starts_at, rebalance=rebalance)
    if moment is None:
        return None
    if any(opens <= moment <= closes for opens, closes in bounds):
        earliest = moment
    else:
        earliest = min((opens for opens, _ in bounds if opens > moment), default=None)
    if earliest is None:
        return None
    return min(
        (closes for _, closes in bounds if closes >= earliest and closes > starts_at),
        default=None,
    )


def first_rebalance_at(rebalance: Any, *, starts_at: dt.datetime) -> dt.datetime | None:
    """The portfolio's first rebalance for a signal first observed at ``starts_at``.

    Weights observed later take effect by the same rule, so ``starts_at`` is also a later
    observation's time: its answer is when those weights take effect (issue #48). None when the
    stored calendar has no rebalance in the ``FIRST_REBALANCE_SEARCH_DAYS`` after the weights,
    or does not cover those dates.
    """
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
    return first_trade_at(sessions, starts_at=starts_at, rebalance=rebalance)


def describe_event(rebalance: Any) -> str:
    """The configured event in words, as ``NYSE market_close`` or ``NYSE close after its first
    weights``."""
    if rebalance.strategy == "immediate_signal":
        return f"{rebalance.calendar_identifier} close after its first weights"
    return f"{rebalance.calendar_identifier} {rebalance.rebalance_event}"


def rebalance_state(
    rebalance: Any,
    *,
    first: dt.datetime | None,
    at: dt.datetime,
) -> dict[str, Any]:
    """Whether a run at ``at`` has reached a first rebalance at ``first``, already computed."""
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


def first_rebalance_state(
    rebalance: Any,
    *,
    starts_at: dt.datetime,
    at: dt.datetime,
) -> dict[str, Any]:
    """Whether a run at ``at`` has reached the portfolio's first rebalance, and when it is."""
    return rebalance_state(
        rebalance, first=first_rebalance_at(rebalance, starts_at=starts_at), at=at
    )


def recorded_rebalance_state(state: dict[str, Any], recorded: bool) -> dict[str, Any]:
    """A due first rebalance as Markets holds it (ms-markets issue 118): a run reports what
    Markets holds, not what the schedule expected."""
    if recorded or not state["rebalanced"]:
        return state
    return {
        **state,
        "rebalanced": False,
        "message": (
            "The portfolio's first rebalance was due at "
            f"{state['first_rebalance_at']}, and Markets holds no weights of it: "
            "no run has recorded that rebalance."
        ),
    }


def _utc(value: Any) -> dt.datetime | None:
    """A time as an aware UTC datetime; a naive one is UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def portfolio_rebalance_times(
    rebalance: Any,
    *,
    first_observation_at: dt.datetime | None,
    last_observation_at: dt.datetime | None,
    portfolio_identifier: str | None,
    weights_effective: bool = True,
) -> PortfolioRebalanceTimes:
    """When the portfolio's first weights and its latest weights take effect, and whether Markets
    holds them (issue #48).

    Both times follow ``first_rebalance_at``: the latest weights take effect at the rebalance
    after them, as the first weights do. The calendar reads and the read of the portfolio's
    weights bounds in Markets run together; ``portfolio_identifier`` None (a portfolio no run can
    have reached yet) reads no bounds, and nothing is recorded. ``weights_effective`` False leaves
    the latest weights out, for a caller that needs only the first rebalance.
    """
    from alpaca_connectors.markets_api import all_together, portfolio_weights_bounds

    def rebalance_after(observed_at: dt.datetime | None) -> dt.datetime | None:
        if observed_at is None:
            return None
        return first_rebalance_at(rebalance, starts_at=observed_at)

    # One calendar read when the latest weights are the first, or are not asked for.
    separate = weights_effective and last_observation_at != first_observation_at
    first, effective, bounds = all_together(
        lambda: rebalance_after(first_observation_at),
        lambda: rebalance_after(last_observation_at) if separate else None,
        lambda: (
            portfolio_weights_bounds(portfolio_identifier)
            if portfolio_identifier is not None
            else None
        ),
    )
    if weights_effective and not separate:
        effective = first
    row_count = int(getattr(bounds, "row_count", 0) or 0)
    last_time = _utc(getattr(bounds, "last_time", None))
    return PortfolioRebalanceTimes(
        first_rebalance_at=first,
        first_rebalance_recorded=row_count > 0,
        weights_effective_at=effective,
        weights_effective_recorded=(
            effective is not None and last_time is not None and last_time >= effective
        ),
    )


def configuration_rebalance_times(
    configuration: Any,
    *,
    rebalance: Any = None,
    weights_effective: bool = True,
) -> PortfolioRebalanceTimes:
    """``portfolio_rebalance_times`` of a stored configuration, read from what it names.

    Its rebalance configuration (unless ``rebalance`` is given) and its signal's observation
    bounds are read together. A configuration with no ``Portfolio`` yet has nothing recorded.
    """
    from alpaca_connectors.markets_api import all_together
    from alpaca_connectors.portfolios.configurations import get_rebalance_configuration
    from alpaca_connectors.portfolios.provided_weights import (
        portfolio_signal_uid,
        provided_portfolio_unique_identifier,
    )
    from alpaca_connectors.portfolios.signal_observations import read_signal_observation_bounds

    rule, bounds = all_together(
        lambda: (
            rebalance
            if rebalance is not None
            else get_rebalance_configuration(configuration.rebalance_configuration_uid)
        ),
        lambda: read_signal_observation_bounds(signal_uid=portfolio_signal_uid(configuration)),
    )
    if rule is None:
        raise RuntimeError(
            f"Portfolio configuration {configuration.uid!s} has an unresolved required relationship."
        )
    return portfolio_rebalance_times(
        rule,
        first_observation_at=bounds.first_observation_at,
        last_observation_at=bounds.last_observation_at,
        portfolio_identifier=(
            provided_portfolio_unique_identifier(configuration.uid)
            if configuration.portfolio_uid is not None
            else None
        ),
        weights_effective=weights_effective,
    )


def run_first_rebalance(
    rebalance: Any,
    *,
    times: PortfolioRebalanceTimes,
    status: str,
    started_at: Any,
) -> dict[str, Any] | None:
    """A run's ``first_rebalance`` in the run history, as the run's summary states it.

    Only a run that succeeded has one, from when it started: whether it reached the first
    rebalance, and whether Markets holds that rebalance's weights. None for any other run.
    """
    started = _utc(started_at)
    if status != "SUCCEEDED" or started is None:
        return None
    state = rebalance_state(rebalance, first=times.first_rebalance_at, at=started)
    return recorded_rebalance_state(state, times.first_rebalance_recorded)


def pending_first_rebalance(
    portfolio_identifier: str, *, portfolio_uid: str
) -> PendingFirstRebalance | None:
    """The portfolio's first rebalance when that is all its missing weights wait for (issue #48).

    For a sync that found no weights of the portfolio: it is this application's portfolio, its
    signal has weights, and its calendar has a first rebalance after them, which no run has
    recorded by the sync. None when this application cannot say so: a portfolio it does not
    know, whose rebalance configuration is missing, whose signal has published nothing, or whose
    calendar has no rebalance in the search window; the sync then fails with
    ``portfolio_weights_missing``.
    """
    from alpaca_connectors.markets_api import all_together
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
    rebalance, bounds = all_together(
        lambda: get_rebalance_configuration(configuration.rebalance_configuration_uid),
        lambda: read_signal_observation_bounds(signal_uid=portfolio_signal_uid(configuration)),
    )
    if rebalance is None or bounds.first_observation_at is None:
        return None
    first = first_rebalance_at(rebalance, starts_at=bounds.first_observation_at)
    if first is None:
        return None
    return PendingFirstRebalance(portfolio_uid=str(portfolio_uid), first_rebalance_at=first)


__all__ = [
    "FIRST_REBALANCE_SEARCH_DAYS",
    "PendingFirstRebalance",
    "PortfolioRebalanceTimes",
    "configuration_rebalance_times",
    "describe_event",
    "first_rebalance_at",
    "first_rebalance_event",
    "first_rebalance_state",
    "first_trade_at",
    "pending_first_rebalance",
    "portfolio_rebalance_times",
    "rebalance_state",
    "recorded_rebalance_state",
    "run_first_rebalance",
]
