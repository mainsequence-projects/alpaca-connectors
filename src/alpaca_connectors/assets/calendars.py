"""Stored trading calendars for the assets and portfolios of this connector (ADR 0026).

ms-markets 4 links every asset that has price bars to one stored calendar (`AssetCalendar`, its
ADR 0056), and every `Portfolio` row references one (`calendar_uid`). A registration names the
calendar by `unique_identifier`, and ms-markets refuses one it does not store, so the calendar is
stored before it is named. `ensure_trading_calendar` returns the stored calendar of a key, writing
it once when it is missing, with Markets registry row calls only (`Calendar.filter`,
`Calendar.create_from_pandas_calendar`), which run on the Markets API.

A stored calendar is session rules and exceptions written to the last date pandas-market-calendars
can answer for (ms-markets ADR 0055), so nothing extends it as time passes. ms-markets requires
pandas-market-calendars, so this needs nothing more installed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Stored calendar keys whose sessions are the library's always-open `24/7` calendar: every day,
# closing at the next UTC midnight. `CRYPTO_24_7` is the one Alpaca's crypto classes trade on.
ALWAYS_OPEN_CALENDAR_KEYS = ("24/7", "CRYPTO_24_7")

_ALWAYS_OPEN_LIBRARY_CALENDAR = "24/7"


# The calendars this process has seen stored. A stored calendar is never deleted, so once seen it
# is not read again in this process.
_STORED_CALENDARS: dict[str, Any] = {}


def ensure_trading_calendars_stored(calendar_keys: Iterable[str]) -> dict[str, Any]:
    """Return the stored ms-markets Calendar of each key, writing a missing one once.

    The keys this process has not seen yet are read in one `Calendar.filter`
    (`unique_identifier__in`); a key already seen costs nothing. A missing calendar is written with
    `Calendar.create_from_pandas_calendar`: from the library calendar of the same name, or from the
    library's `24/7` calendar for `ALWAYS_OPEN_CALENDAR_KEYS`. An existing calendar is never
    written again: taking it to a newer library release is the Markets deployment's
    `resync-calendars` Job.
    """
    from msm.api.calendars import Calendar

    keys = list(dict.fromkeys(str(key) for key in calendar_keys))
    unseen = [key for key in keys if key not in _STORED_CALENDARS]
    if unseen:
        for row in Calendar.filter(unique_identifier__in=unseen, limit=len(unseen)):
            _STORED_CALENDARS[str(row.unique_identifier)] = row
        for key in unseen:
            if key not in _STORED_CALENDARS:
                _STORED_CALENDARS[key] = _create_calendar(key)
    return {key: _STORED_CALENDARS[key] for key in keys}


def ensure_trading_calendar(calendar_key: str) -> Any:
    """Return the stored ms-markets Calendar whose `unique_identifier` is `calendar_key`."""
    return ensure_trading_calendars_stored([calendar_key])[str(calendar_key)]


def _create_calendar(calendar_key: str) -> Any:
    from msm.api.calendars import Calendar, CalendarType

    always_open = calendar_key in ALWAYS_OPEN_CALENDAR_KEYS
    return Calendar.create_from_pandas_calendar(
        _ALWAYS_OPEN_LIBRARY_CALENDAR if always_open else calendar_key,
        calendar_key,
        display_name=(
            f"{calendar_key} always-open sessions"
            if always_open
            else f"{calendar_key} trading sessions"
        ),
        calendar_type=CalendarType.TRADING,
    )


__all__ = [
    "ALWAYS_OPEN_CALENDAR_KEYS",
    "ensure_trading_calendar",
    "ensure_trading_calendars_stored",
]
