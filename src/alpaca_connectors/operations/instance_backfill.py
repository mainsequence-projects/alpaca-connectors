"""One-off copy of this application's rows from the shared Markets time series (ADR 0047).

Before this release the signal, portfolio, calendar-event, interpolated-price, order and trade
updaters wrote into the shared ``ms_markets__*`` tables. They now write into this application's
instances (``alpaca_connectors.instances``, revisions 0020 and 0021). This module copies the rows
that belong to this application into those instances, once, so their history survives the move:

- signal weights of every signal this application published (signal Job configurations, and the
  signal each of its portfolios points to). Signal observations are live ETF extractions that
  cannot be recomputed, so they must be copied, never rebuilt;
- portfolio values, weights and rebalance state of its portfolios
  (``ALPACA_ETF_PORTFOLIO__<configuration>``);
- calendar events of the calendars its rebalance configurations use;
- every row of the interpolated-price tables revision 0011 created;
- orders and trades of its registered accounts.

It is the only module that reads Markets tables with compiled SQL, so it runs as a person who
can read them (``data_viewer`` on the source tables in ``mainsequence.markets``) and write this
application's namespace, from ``alpaca-connectors instances backfill`` after the migration Job
has applied revision 0021 and before the new release's signal and portfolio Jobs run. Rows are
copied in pages ordered by each table's grain and inserted with ``ON CONFLICT DO NOTHING``, so
the command is idempotent and can be rerun after a failure. See
docs/operations/markets-api-boundary.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_BATCH_SIZE = 2_000


@dataclass(frozen=True)
class BackfillPair:
    """One source table, its destination instance, and the rows of it that are this app's."""

    label: str
    source: Any
    destination: Any
    scope_column: str | None
    scope_values: tuple[str, ...]

    @property
    def grain(self) -> list[str]:
        return list(self.destination.__index_names__)

    def summary(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source": self.source.__table__.name,
            "destination": self.destination.__table__.name,
            "scope_column": self.scope_column,
            "scope_size": None if self.scope_column is None else len(self.scope_values),
        }


def _own_rows(statement: Any) -> list[dict[str, Any]]:
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation

    from alpaca_connectors.runtime import start_markets_engine

    runtime = start_markets_engine(models=backfill_runtime_models())
    return operation_result_rows(
        execute_markets_operation(
            compile_markets_statement(statement, context=runtime.context, operation="select"),
            context=runtime.context,
        )
    )


def _scope() -> dict[str, tuple[str, ...]]:
    """This application's signal uids, portfolio identifiers, calendars and accounts."""
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

    signal_configurations = [
        AlpacaETFSignalJobConfiguration.model_validate(row)
        for row in _own_rows(select(AlpacaETFSignalJobConfigurationTable))
    ]
    portfolio_identifiers = sorted(
        portfolio_unique_identifier(row["uid"])
        for row in _own_rows(select(AlpacaETFPortfolioConfigurationTable.uid))
    )
    portfolios = Portfolio.filter(
        unique_identifier__in=portfolio_identifiers, limit=max(len(portfolio_identifiers), 1)
    )
    signal_uids = {signal_uid_for_configuration(item) for item in signal_configurations}
    signal_uids.update(str(item.signal_uid) for item in portfolios if item.signal_uid)
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
        "signal_uids": tuple(sorted(signal_uids)),
        "portfolio_identifiers": tuple(portfolio_identifiers),
        "calendar_identifiers": tuple(sorted(calendars)),
        "account_identifiers": tuple(sorted(accounts)),
    }


def _interpolated_pairs() -> list[BackfillPair]:
    """Revision 0011's Markets-namespace interpolated tables and their namespaced successors."""
    from msm_portfolios.time_index.prices.storage import configured_interpolated_prices_storage

    from alpaca_connectors.portfolios.interpolated_prices_schema import (
        INTERPOLATION_RULE,
        UPSAMPLE_FREQUENCY_ID,
        configured_alpaca_interpolated_prices_storage,
        resolve_interpolated_prices_storage_specs,
    )

    pairs = []
    for spec in resolve_interpolated_prices_storage_specs():
        identity = {
            "source_time_index_meta_table_uid": spec.source_time_index_meta_table_uid,
            "source_cadence": spec.source_cadence,
            "upsample_frequency_id": UPSAMPLE_FREQUENCY_ID,
            "intraday_bar_interpolation_rule": INTERPOLATION_RULE,
        }
        pairs.append(
            BackfillPair(
                label=f"interpolated_prices[{spec.frequency_id}/{spec.feed}/{spec.adjustment}]",
                source=configured_interpolated_prices_storage(**identity),
                destination=configured_alpaca_interpolated_prices_storage(**identity),
                scope_column=None,
                scope_values=(),
            )
        )
    return pairs


def backfill_pairs() -> list[BackfillPair]:
    """Every copy this backfill performs, with this application's scope resolved."""
    from msm.time_index.execution.storage import OrdersStorage, TradesStorage
    from msm_portfolios.time_index.portfolios.storage import (
        PortfolioCalendarEventsStorage,
        PortfolioRebalanceStateStorage,
        PortfoliosStorage,
        PortfolioWeightsStorage,
    )
    from msm_portfolios.time_index.signals.storage import SignalWeightsStorage

    from alpaca_connectors import instances

    scope = _scope()
    return [
        BackfillPair(
            "signal_weights",
            SignalWeightsStorage,
            instances.AlpacaSignalWeightsStorage,
            "signal_uid",
            scope["signal_uids"],
        ),
        BackfillPair(
            "portfolio_values",
            PortfoliosStorage,
            instances.AlpacaPortfoliosStorage,
            "portfolio_identifier",
            scope["portfolio_identifiers"],
        ),
        BackfillPair(
            "portfolio_weights",
            PortfolioWeightsStorage,
            instances.AlpacaPortfolioWeightsStorage,
            "portfolio_identifier",
            scope["portfolio_identifiers"],
        ),
        BackfillPair(
            "portfolio_rebalance_state",
            PortfolioRebalanceStateStorage,
            instances.AlpacaPortfolioRebalanceStateStorage,
            "portfolio_identifier",
            scope["portfolio_identifiers"],
        ),
        BackfillPair(
            "portfolio_calendar_events",
            PortfolioCalendarEventsStorage,
            instances.AlpacaPortfolioCalendarEventsStorage,
            "calendar_identifier",
            scope["calendar_identifiers"],
        ),
        *_interpolated_pairs(),
        BackfillPair(
            "orders",
            OrdersStorage,
            instances.AlpacaOrdersStorage,
            "account_identifier",
            scope["account_identifiers"],
        ),
        BackfillPair(
            "trades",
            TradesStorage,
            instances.AlpacaTradesStorage,
            "account_identifier",
            scope["account_identifiers"],
        ),
    ]


def backfill_runtime_models() -> list[type[Any]]:
    """This application's tables plus the Markets sources the backfill reads.

    The only runtime that attaches Markets time-series tables; it needs the operator's read
    access to them.
    """
    from msm.time_index.execution.storage import OrdersStorage, TradesStorage
    from msm_portfolios.time_index.portfolios.storage import (
        PortfolioCalendarEventsStorage,
        PortfolioRebalanceStateStorage,
        PortfoliosStorage,
        PortfolioWeightsStorage,
    )
    from msm_portfolios.time_index.signals.storage import SignalWeightsStorage

    from alpaca_connectors.runtime import portfolio_job_runtime_models

    return [
        *portfolio_job_runtime_models(),
        SignalWeightsStorage,
        PortfoliosStorage,
        PortfolioWeightsStorage,
        PortfolioRebalanceStateStorage,
        PortfolioCalendarEventsStorage,
        OrdersStorage,
        TradesStorage,
        *(pair.source for pair in _interpolated_pairs()),
    ]


def _scoped(statement: Any, pair: BackfillPair) -> Any:
    if pair.scope_column is None:
        return statement
    return statement.where(pair.source.__table__.c[pair.scope_column].in_(pair.scope_values))


def _count(context: Any, table: Any, pair: BackfillPair) -> int:
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, select

    statement = select(func.count().label("count")).select_from(table)
    if pair.scope_column is not None:
        statement = statement.where(table.c[pair.scope_column].in_(pair.scope_values))
    rows = operation_result_rows(
        execute_markets_operation(
            compile_markets_statement(statement, context=context, operation="select"),
            context=context,
        )
    )
    return int(rows[0].get("count", 0)) if rows else 0


def _copy_pages(context: Any, pair: BackfillPair, *, batch_size: int, total: int) -> int:
    """Copy ``total`` scoped source rows in grain order, one insert per page."""
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    source = pair.source.__table__
    destination = pair.destination.__table__
    columns = [column.name for column in destination.columns]

    def copy_page(offset: int) -> int:
        statement = _scoped(select(*(source.c[name] for name in columns)), pair)
        statement = statement.order_by(*(source.c[name] for name in pair.grain))
        page = operation_result_rows(
            execute_markets_operation(
                compile_markets_statement(
                    statement.limit(batch_size).offset(offset), context=context, operation="select"
                ),
                context=context,
            )
        )
        if not page:
            return 0
        write = (
            insert(destination)
            .values([{name: row.get(name) for name in columns} for row in page])
            .on_conflict_do_nothing(index_elements=pair.grain)
        )
        execute_markets_operation(
            compile_markets_statement(write, context=context, operation="insert"),
            context=context,
        )
        return len(page)

    return sum(copy_page(offset) for offset in range(0, total, batch_size))


def backfill_instances(
    *,
    execute: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    register_catalog: bool = False,
) -> dict[str, Any]:
    """Copy this application's rows into its instances; a dry run reports the counts only.

    With ``register_catalog`` (after ``execute``), registers and reconciles the Markets dataset
    catalog row of every portfolio this application computed and binds every copied signal,
    which needs the Markets workload's ``data_viewer`` on the instance tables.
    """
    from alpaca_connectors.runtime import start_markets_engine

    if batch_size < 1:
        raise ValueError("batch_size must be positive.")
    runtime = start_markets_engine(models=backfill_runtime_models())
    context = runtime.context
    results = []
    for pair in backfill_pairs():
        empty_scope = pair.scope_column is not None and not pair.scope_values
        source_rows = 0 if empty_scope else _count(context, pair.source.__table__, pair)
        before = 0 if empty_scope else _count(context, pair.destination.__table__, pair)
        copied = (
            _copy_pages(context, pair, batch_size=batch_size, total=source_rows)
            if execute and source_rows
            else 0
        )
        results.append(
            {
                **pair.summary(),
                "source_rows": source_rows,
                "destination_rows_before": before,
                "rows_read" if execute else "rows_to_read": copied if execute else source_rows,
            }
        )
    catalog = _register_catalog() if execute and register_catalog else None
    return {"executed": execute, "batch_size": batch_size, "tables": results, "catalog": catalog}


def _register_catalog() -> dict[str, Any]:
    from msm.api.portfolios import Portfolio

    from alpaca_connectors.portfolios.datasets import (
        register_portfolio_datasets,
        register_signal_dataset,
    )

    scope = _scope()
    identifiers = list(scope["portfolio_identifiers"])
    portfolios = Portfolio.filter(unique_identifier__in=identifiers, limit=max(len(identifiers), 1))
    registered = [str(register_portfolio_datasets(item.uid).portfolio_uid) for item in portfolios]
    bound = [str(register_signal_dataset(uid).signal_uid) for uid in scope["signal_uids"]]
    return {"portfolios": registered, "signals": bound}


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "BackfillPair",
    "backfill_instances",
    "backfill_pairs",
    "backfill_runtime_models",
]
