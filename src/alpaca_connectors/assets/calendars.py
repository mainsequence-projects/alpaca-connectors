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

from typing import Any

# Stored calendar keys whose sessions are the library's always-open `24/7` calendar: every day,
# closing at the next UTC midnight. `CRYPTO_24_7` is the one Alpaca's crypto classes trade on.
ALWAYS_OPEN_CALENDAR_KEYS = ("24/7", "CRYPTO_24_7")

_ALWAYS_OPEN_LIBRARY_CALENDAR = "24/7"


def ensure_trading_calendar(calendar_key: str) -> Any:
    """Return the stored ms-markets Calendar whose `unique_identifier` is `calendar_key`.

    One `Calendar.filter` read when it exists. A missing one is written once with
    `Calendar.create_from_pandas_calendar`: from the library calendar of the same name, or from
    the library's `24/7` calendar for `ALWAYS_OPEN_CALENDAR_KEYS`. An existing calendar is never
    written again: taking it to a newer library release is the Markets deployment's
    `resync-calendars` Job.
    """
    from msm.api.calendars import Calendar, CalendarType

    existing = Calendar.filter(unique_identifier=calendar_key, limit=1)
    if existing:
        return existing[0]
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


__all__ = ["ALWAYS_OPEN_CALENDAR_KEYS", "ensure_trading_calendar"]
