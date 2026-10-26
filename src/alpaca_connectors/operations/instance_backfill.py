"""Move this application's history from the shared Markets time series into its instances.

Before this release the signal, portfolio, calendar-event, interpolated-price, order and trade
updaters wrote into the shared ``ms_markets__*`` tables (and revision 0011's interpolated-price
tables in the Markets namespace). They now write into this application's instances
(``alpaca_connectors.instances``, revisions 0020 and 0021). The history already written is moved,
and can be rolled back, with ms-markets' shared series moves (``msm.storage_copies``,
``msm_portfolios.services.series_moves``):

- ``SignalSeriesMove`` for the signals of this application's signal configurations that no
  portfolio of it currently points to. Signal observations are live ETF extractions that cannot be
  recomputed, so they are copied, never rebuilt;
- ``PortfolioSeriesMove`` per ``ALPACA_ETF_PORTFOLIO__*`` portfolio: its current signal's weights,
  rebalance state, weights and values, then the Markets catalog row (``register``) last;
- ``TableSeriesMove`` for the calendar events of its rebalance calendars, the interpolated prices
  of each bars profile (keyed by asset) and the orders and trades of its registered accounts.

Each copy writes only the rows the target lacks (by the full index) and verifies every key; an
incomplete move changes nothing in Markets and a rerun fills the gaps. A rollback copies the rows
the instances gained back into the shared tables and removes the catalog rows and signal bindings.
This module reads no Markets table itself: it selects what to move from this application's own
tables, and ms-markets reads and writes through MetaTables as the person who runs it, who needs
``data_viewer`` on the tables read and write access on the tables written (a rollback writes the
shared tables). Run it from ``alpaca-connectors instances backfill|rollback`` or ``msm series
move|rollback alpaca_connectors.operations.instance_backfill:series_moves``; see
docs/operations/markets-api-boundary.md.
"""

from __future__ import annotations

from typing import Any


def _own_rows(statement: Any) -> list[dict[str, Any]]:
    """Rows of a statement on this application's own tables."""
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation

    from alpaca_connectors.runtime import start_markets_engine

    runtime = start_markets_engine()
    return operation_result_rows(
        execute_markets_operation(
            compile_markets_statement(statement, context=runtime.context, operation="select"),
            context=runtime.context,
        )
    )


def _scope() -> dict[str, Any]:
    """This application's portfolios, signals, calendars and accounts, from its own tables."""
    from msm.api.portfolios import Portfolio
    from sqlalchemy import select

    from alpaca_connectors.account.alpaca_account_details import AlpacaAccountDetails
    from alpaca_connectors.operations.signal_job_configurations import (
        AlpacaETFSignalJobConfiguration,
        AlpacaETFSignalJobConfigurationTable,
    )
    from alpaca_connectors.operations.signal_jobs import signal_uid_for_configuration
    from alpaca_connectors.portfolios.configurations import (
        AlpacaETFPortfolioConfigurationTable,
        PortfolioRebalanceConfigurationTable,
    )
    from alpaca_connectors.portfolios.execution import portfolio_unique_identifier

    identifiers = sorted(
        portfolio_unique_identifier(row["uid"])
        for row in _own_rows(select(AlpacaETFPortfolioConfigurationTable.uid))
    )
    # A configuration that never ran has no Portfolio row and no rows to move.
    portfolios = Portfolio.filter(unique_identifier__in=identifiers, limit=max(len(identifiers), 1))
    configured_signals = {
        signal_uid_for_configuration(AlpacaETFSignalJobConfiguration.model_validate(row))
        for row in _own_rows(select(AlpacaETFSignalJobConfigurationTable))
    }
    # A portfolio's current signal moves with its portfolio; the others move on their own.
    portfolio_signals = {str(item.signal_uid) for item in portfolios if item.signal_uid}
    calendars = {
        str(row["calendar_identifier"])
        for row in _own_rows(select(PortfolioRebalanceConfigurationTable.calendar_identifier))
    }
    details = AlpacaAccountDetails.__table__
    accounts = {
        str(row["account_unique_identifier"])
        for row in _own_rows(select(details.c.account_unique_identifier))
    }
    return {
        "portfolios": sorted(portfolios, key=lambda item: str(item.unique_identifier)),
        "signal_uids": sorted(configured_signals - portfolio_signals),
        "calendar_identifiers": sorted(calendars),
        "account_identifiers": sorted(accounts),
    }


def _interpolated_moves() -> list[Any]:
    """Revision 0011's Markets-namespace interpolated tables into their namespaced successors.

    The keys are the assets of the bars table each storage interpolates: an interpolated row
    exists only for an asset that has bars.
    """
    from msm.storage_copies import TableSeriesMove
    from msm_portfolios.time_index.prices.storage import configured_interpolated_prices_storage
    from sqlalchemy import select

    from alpaca_connectors.market_data.storage import ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE
    from alpaca_connectors.portfolios.interpolated_prices_schema import (
        INTERPOLATION_RULE,
        UPSAMPLE_FREQUENCY_ID,
        configured_alpaca_interpolated_prices_storage,
        resolve_interpolated_prices_storage_specs,
    )

    moves = []
    for spec in resolve_interpolated_prices_storage_specs():
        bars = ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE[(spec.frequency_id, spec.feed, spec.adjustment)]
        assets = sorted(
            str(row["asset_identifier"])
            for row in _own_rows(select(bars.__table__.c.asset_identifier).distinct())
        )
        identity = {
            "source_time_index_meta_table_uid": spec.source_time_index_meta_table_uid,
            "source_cadence": spec.source_cadence,
            "upsample_frequency_id": UPSAMPLE_FREQUENCY_ID,
            "intraday_bar_interpolation_rule": INTERPOLATION_RULE,
        }
        moves.append(
            TableSeriesMove(
                configured_interpolated_prices_storage(**identity),
                configured_alpaca_interpolated_prices_storage(**identity),
                "asset_identifier",
                assets,
                label=f"interpolated_prices[{spec.frequency_id}/{spec.feed}/{spec.adjustment}]",
            )
        )
    return moves


def build_series_moves(*, register_catalog: bool) -> list[Any]:
    """Every move of this application's history, with its scope resolved.

    ``register_catalog`` writes the Markets catalog rows and signal bindings after a complete
    move (and removes them on a rollback); it needs the Markets workload's ``data_viewer`` on the
    portfolio and signal instances.
    """
    from msm.storage_copies import TableSeriesMove
    from msm.time_index.execution.storage import OrdersStorage, TradesStorage
    from msm_portfolios.services.series_moves import (
        PortfolioSeriesMove,
        PortfolioSeriesTables,
        SignalSeriesMove,
    )
    from msm_portfolios.time_index.portfolios.storage import PortfolioCalendarEventsStorage

    from alpaca_connectors import instances

    scope = _scope()
    portfolio_tables = PortfolioSeriesTables(
        values=instances.AlpacaPortfoliosStorage,
        weights=instances.AlpacaPortfolioWeightsStorage,
        rebalance_state=instances.AlpacaPortfolioRebalanceStateStorage,
        signal_weights=instances.AlpacaSignalWeightsStorage,
    )
    moves: list[Any] = []
    if scope["signal_uids"]:
        moves.append(
            SignalSeriesMove(
                scope["signal_uids"],
                instances.AlpacaSignalWeightsStorage,
                label="signal configurations",
                register=register_catalog,
            )
        )
    moves.extend(
        PortfolioSeriesMove(
            str(portfolio.unique_identifier),
            portfolio_tables,
            portfolio_uid=portfolio.uid,
            with_current_signal=True,
            register=register_catalog,
        )
        for portfolio in scope["portfolios"]
    )
    if scope["calendar_identifiers"]:
        moves.append(
            TableSeriesMove(
                PortfolioCalendarEventsStorage,
                instances.AlpacaPortfolioCalendarEventsStorage,
                "calendar_identifier",
                scope["calendar_identifiers"],
                label="calendar_events",
            )
        )
    if scope["account_identifiers"]:
        moves.append(
            TableSeriesMove(
                OrdersStorage,
                instances.AlpacaOrdersStorage,
                "account_identifier",
                scope["account_identifiers"],
                label="orders",
            )
        )
        moves.append(
            TableSeriesMove(
                TradesStorage,
                instances.AlpacaTradesStorage,
                "account_identifier",
                scope["account_identifiers"],
                label="trades",
            )
        )
    moves.extend(_interpolated_moves())
    return moves


def series_moves() -> list[Any]:
    """``msm series move|rollback alpaca_connectors.operations.instance_backfill:series_moves``.

    The same moves as ``alpaca-connectors instances backfill --register-catalog``.
    """
    return build_series_moves(register_catalog=True)


def backfill_instances(*, execute: bool = False, register_catalog: bool = False) -> dict[str, Any]:
    """Move this application's history into its instances; a dry run only counts."""
    from msm.storage_copies import run_series_moves

    moves = build_series_moves(register_catalog=register_catalog)
    return run_series_moves(moves, dry_run=not execute).summary()


def rollback_instances(*, execute: bool = False, register_catalog: bool = True) -> dict[str, Any]:
    """Copy the rows the instances gained back into the shared tables and unregister them.

    Runs before the Markets roles are revoked (it writes the shared tables). The catalog rows
    and signal bindings are removed unless ``register_catalog`` is false. This release's updaters
    keep writing the instances, and each run registers its catalog row again, so a rollback is
    followed by deploying the previous release before the next run.
    """
    from msm.storage_copies import run_series_moves

    moves = build_series_moves(register_catalog=register_catalog)
    return run_series_moves(moves, rollback=True, dry_run=not execute).summary()


__all__ = [
    "backfill_instances",
    "build_series_moves",
    "rollback_instances",
    "series_moves",
]
