"""The assets an account's ``AccountStockBars`` fetches on one bars profile (ADR 0033).

The asset list is stored nowhere. Each resolution derives it in bounded reads, none of them over a
time series:

1. the account's portfolio configurations, and its ``portfolios`` feeds, together; then the
   portfolios not yet adopted that name one of those feeds (ADR 0028, as amended);
2. together: those portfolios' own bars configurations, their `Portfolio` rows in one Markets read
   for ``retired_at``, and their signal definitions;
3. every asset the signals of the live portfolios on the profile ever published, from the
   signal-asset table by primary key.

A portfolio is priced by its own bars configuration, else by the feed it names. One whose bars
configuration is on another profile, or disabled, is left out; so is one whose `Portfolio` is
retired at or before the resolution time. A paused one still counts, and so does one whose
`Portfolio` does not exist yet. Feeds of explicit assets or holdings resolve to nothing here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

UTC = dt.timezone.utc


@dataclass(frozen=True, slots=True)
class AccountBarsScope:
    account_uid: str
    profile: tuple[str, str, str]
    configuration_uids: tuple[str, ...]
    retired_configuration_uids: tuple[str, ...]
    signal_uids: tuple[str, ...]
    asset_identifiers: tuple[str, ...]

    def snapshot(self) -> dict[str, Any]:
        return {
            "portfolio_count": len(self.configuration_uids),
            "retired_portfolio_count": len(self.retired_configuration_uids),
            "signal_count": len(self.signal_uids),
        }


def retired_portfolio_uids(portfolio_uids: Sequence[str], *, at: dt.datetime) -> set[str]:
    """The listed `Portfolio` uids retired at or before ``at`` (ms-markets ADR 0049)."""
    from msm.api.portfolios import Portfolio

    from alpaca_connectors.markets_api import read_in_batches

    uids = sorted({str(uid) for uid in portfolio_uids})
    if not uids:
        return set()
    rows = read_in_batches(lambda batch: Portfolio.filter(uid__in=batch, limit=len(batch)), uids)
    retired: set[str] = set()
    for row in rows:
        retired_at = getattr(row, "retired_at", None)
        if retired_at is None:
            continue
        if retired_at.tzinfo is None:
            retired_at = retired_at.replace(tzinfo=UTC)
        if retired_at <= at:
            retired.add(str(row.uid))
    return retired


def _normalized_profile(frequency_id: str, feed: str, adjustment: str) -> tuple[str, str, str]:
    from alpaca_connectors.market_data.alpaca_bars_support import (
        normalize_adjustment,
        normalize_feed,
        normalize_frequency_id,
    )

    return (
        normalize_frequency_id(frequency_id),
        normalize_feed(feed),
        normalize_adjustment(adjustment),
    )


def resolve_account_bars_scope(
    account_uid: Any,
    profile: tuple[str, str, str],
    *,
    at: dt.datetime | None = None,
) -> AccountBarsScope:
    """Every asset ever published by the signals of the account's live portfolios on ``profile``."""
    from alpaca_connectors.market_data.configurations import (
        bar_configurations_for_account,
        portfolio_bar_configurations,
    )
    from alpaca_connectors.markets_api import all_together
    from alpaca_connectors.portfolios.configurations import (
        portfolio_configurations_naming_any_bars,
        portfolio_configurations_of_account,
    )
    from alpaca_connectors.portfolios.provided_weights import portfolio_signal_uid
    from alpaca_connectors.portfolios.signal_assets import assets_for_signals
    from alpaca_connectors.portfolios.signal_definitions import signal_definitions_by_uids

    account = str(uuid.UUID(str(account_uid)))
    wanted = _normalized_profile(*profile)
    resolved_at = at or dt.datetime.now(UTC)
    owned, account_feeds = all_together(
        lambda: portfolio_configurations_of_account(account),
        lambda: bar_configurations_for_account(account),
    )
    feeds = {str(feed.uid): feed for feed in account_feeds if feed.asset_source == "portfolios"}
    naming = portfolio_configurations_naming_any_bars(list(feeds)) if feeds else []
    rows = list({str(row.uid): row for row in [*owned, *naming]}.values())
    own_bars, retired, definitions = all_together(
        lambda: portfolio_bar_configurations([row.uid for row in rows]),
        lambda: retired_portfolio_uids(
            [str(row.portfolio_uid) for row in rows if row.portfolio_uid is not None],
            at=resolved_at,
        ),
        lambda: signal_definitions_by_uids(row.signal_definition_uid for row in rows),
    )
    live: list[Any] = []
    retired_configurations: list[str] = []
    for row in rows:
        bars = own_bars.get(str(row.uid))
        if bars is None and row.bars_configuration_uid is not None:
            bars = feeds.get(str(row.bars_configuration_uid))
        if bars is None or not bars.enabled:
            continue
        if _normalized_profile(bars.frequency_id, bars.feed, bars.adjustment) != wanted:
            continue
        if row.portfolio_uid is not None and str(row.portfolio_uid) in retired:
            retired_configurations.append(str(row.uid))
        else:
            live.append(row)
    signals = tuple(sorted({portfolio_signal_uid(row, definitions=definitions) for row in live}))
    return AccountBarsScope(
        account_uid=account,
        profile=wanted,
        configuration_uids=tuple(sorted(str(row.uid) for row in live)),
        retired_configuration_uids=tuple(sorted(retired_configurations)),
        signal_uids=signals,
        asset_identifiers=tuple(assets_for_signals(signals)) if signals else (),
    )


__all__ = ["AccountBarsScope", "resolve_account_bars_scope", "retired_portfolio_uids"]
