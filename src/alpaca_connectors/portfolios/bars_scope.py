"""The assets a `portfolios` bars configuration fetches (ADR 0024, decision 5).

The asset list is stored nowhere. Each resolution derives it in three bounded reads, none of them
over a time series and none per portfolio:

1. the portfolio configurations that name the bars configuration (indexed on
   ``bars_configuration_uid``), reading only the columns it needs;
2. their `Portfolio` rows by uid in one batched Markets read, for ``retired_at``, and the ETF
   rows' Signal Configurations in one query; signal UIDs are then computed in memory;
3. every asset those signals ever published, from the signal-asset table by primary key.

A deleted configuration has no row. A configuration whose `Portfolio` is retired at or before
the resolution time is left out. A paused one still counts, and so does one whose `Portfolio`
does not exist yet.
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


def _naming_configurations(bars_configuration_uid: uuid.UUID) -> list[dict[str, Any]]:
    from msm.api.base import operation_result_rows
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from alpaca_connectors.portfolios.configurations import (
        AlpacaETFPortfolioConfigurationTable,
        _start_runtime,
    )

    table = AlpacaETFPortfolioConfigurationTable
    _start_runtime()
    runtime = resolve_runtime(models=[table], row_model_name="AlpacaETFPortfolioConfiguration")
    operation = compile_markets_statement(
        select(
            table.uid,
            table.weights_source,
            table.signal_configuration_uid,
            table.portfolio_uid,
        ).where(table.bars_configuration_uid == bars_configuration_uid),
        context=runtime.context,
        operation="select",
    )
    return list(
        operation_result_rows(execute_markets_operation(operation, context=runtime.context))
    )


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
    """Every asset ever published by the live portfolios that name this bars configuration."""
    from alpaca_connectors.operations import (
        signal_job_configurations_by_uids,
        signal_uid_for_configuration,
    )
    from alpaca_connectors.portfolios.provided_weights import provided_signal_uid
    from alpaca_connectors.portfolios.signal_assets import assets_for_signals

    normalized_uid = uuid.UUID(str(bars_configuration_uid))
    resolved_at = at or dt.datetime.now(UTC)
    rows = _naming_configurations(normalized_uid)
    retired = retired_portfolio_uids(
        [str(row["portfolio_uid"]) for row in rows if row.get("portfolio_uid") is not None],
        at=resolved_at,
    )
    live: list[dict[str, Any]] = []
    retired_configurations: list[str] = []
    for row in rows:
        portfolio_uid = row.get("portfolio_uid")
        if portfolio_uid is not None and str(portfolio_uid) in retired:
            retired_configurations.append(str(row["uid"]))
        else:
            live.append(row)
    etf_signal_configurations = signal_job_configurations_by_uids(
        sorted(
            {
                str(row["signal_configuration_uid"])
                for row in live
                if row.get("weights_source", "etf_signal") == "etf_signal"
                and row.get("signal_configuration_uid") is not None
            }
        )
    )
    signal_uids: set[str] = set()
    for row in live:
        if row.get("weights_source") == "provided":
            signal_uids.add(provided_signal_uid(row["uid"]))
            continue
        signal_configuration = etf_signal_configurations.get(str(row["signal_configuration_uid"]))
        if signal_configuration is not None:
            signal_uids.add(signal_uid_for_configuration(signal_configuration))
    ordered_signals = tuple(sorted(signal_uids))
    return PortfolioBarsScope(
        bars_configuration_uid=str(normalized_uid),
        configuration_uids=tuple(sorted(str(row["uid"]) for row in live)),
        retired_configuration_uids=tuple(sorted(retired_configurations)),
        signal_uids=ordered_signals,
        asset_identifiers=tuple(assets_for_signals(ordered_signals)),
    )


__all__ = ["PortfolioBarsScope", "resolve_portfolio_bars_scope", "retired_portfolio_uids"]
