"""The assets a portfolio's own bars configuration fetches (ADR 0024 decision 5, ADR 0028).

The asset list is stored nowhere. Each resolution derives it in bounded reads, none of them over a
time series:

1. the bars configuration and the portfolio configuration it belongs to;
2. that portfolio's `Portfolio` row in one Markets read, for ``retired_at``; the signal UID is
   computed from its signal definition's key;
3. every asset that signal ever published, from the signal-asset table by primary key.

A bars feed of an account, or a ``portfolios`` configuration a portfolio not yet adopted still
names, has no portfolio of its own and resolves to nothing. A portfolio whose `Portfolio` is
retired at or before the resolution time is left out. A paused one still counts, and so does one
whose `Portfolio` does not exist yet.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

UTC = dt.timezone.utc


@dataclass(frozen=True, slots=True)
class PortfolioBarsScope:
    bars_configuration_uid: str
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


def _owning_configurations(bars_configuration_uid: uuid.UUID) -> list[Any]:
    """The adopted portfolio configuration the bars configuration belongs to, if any."""
    from alpaca_connectors.market_data.configurations import get_bar_configuration
    from alpaca_connectors.portfolios.configurations import get_portfolio_configuration

    bars = get_bar_configuration(bars_configuration_uid)
    if bars is None or bars.portfolio_configuration_uid is None:
        return []
    configuration = get_portfolio_configuration(bars.portfolio_configuration_uid)
    if configuration is None or not configuration.adopted:
        return []
    return [configuration]


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


def resolve_portfolio_bars_scope(
    bars_configuration_uid: Any,
    *,
    at: dt.datetime | None = None,
) -> PortfolioBarsScope:
    """Every asset ever published by the signal of the live portfolio that owns the configuration."""
    from alpaca_connectors.portfolios.provided_weights import portfolio_signal_uid
    from alpaca_connectors.portfolios.signal_assets import assets_for_signals

    normalized_uid = uuid.UUID(str(bars_configuration_uid))
    resolved_at = at or dt.datetime.now(UTC)
    rows = _owning_configurations(normalized_uid)
    retired = retired_portfolio_uids(
        [str(row.portfolio_uid) for row in rows if row.portfolio_uid is not None],
        at=resolved_at,
    )
    live: list[Any] = []
    retired_configurations: list[str] = []
    for row in rows:
        if row.portfolio_uid is not None and str(row.portfolio_uid) in retired:
            retired_configurations.append(str(row.uid))
        else:
            live.append(row)
    ordered_signals = tuple(sorted({portfolio_signal_uid(row) for row in live}))
    return PortfolioBarsScope(
        bars_configuration_uid=str(normalized_uid),
        configuration_uids=tuple(sorted(str(row.uid) for row in live)),
        retired_configuration_uids=tuple(sorted(retired_configurations)),
        signal_uids=ordered_signals,
        asset_identifiers=tuple(assets_for_signals(ordered_signals)),
    )


__all__ = ["PortfolioBarsScope", "resolve_portfolio_bars_scope", "retired_portfolio_uids"]
