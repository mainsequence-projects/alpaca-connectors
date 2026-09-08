"""Resolve and run durable ETF portfolio configurations."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from etfhextractor.portfolio_publish import ensure_trading_calendar

from src.portfolios.alpaca_etf_signal import build_alpaca_etf_holdings_signal
from src.portfolios.configurations import (
    AlpacaETFPortfolioConfiguration,
    PortfolioRebalanceConfiguration,
    get_portfolio_configuration,
    update_portfolio_configuration_row,
    validate_portfolio_references,
)
from src.portfolios.etf_tracking import (
    build_alpaca_interpolated_prices,
    resolve_alpaca_bars_time_index_meta_table_uid,
)
from src.portfolios.signal_history import (
    read_signal_observation_bounds,
    read_signal_observation_matrix,
)


@dataclass(frozen=True, slots=True)
class ResolvedPortfolioConfiguration:
    configuration: AlpacaETFPortfolioConfiguration
    signal_configuration: Any
    bars_configuration: Any
    rebalance_configuration: PortfolioRebalanceConfiguration
    signal_uid: str
    signal_start_time: dt.datetime
    asset_identifiers: tuple[str, ...]
    source_time_index_meta_table_uid: str

    def summary(self) -> dict[str, Any]:
        return {
            "configuration_uid": str(self.configuration.uid),
            "signal_configuration_uid": str(self.signal_configuration.uid),
            "bars_configuration_uid": str(self.bars_configuration.uid),
            "rebalance_configuration_uid": str(self.rebalance_configuration.uid),
            "signal_uid": self.signal_uid,
            "signal_start_time": self.signal_start_time.isoformat(),
            "asset_count": len(self.asset_identifiers),
            "source_time_index_meta_table_uid": self.source_time_index_meta_table_uid,
            "rebalance_strategy": self.rebalance_configuration.strategy,
        }


@dataclass(frozen=True, slots=True)
class PortfolioExecutionResult:
    resolved: ResolvedPortfolioConfiguration
    portfolio_uid: str
    portfolio_unique_identifier: str
    interpolation_result: Any
    calendar_events_result: Any
    rebalance_result: Any
    portfolio_weights_result: Any
    portfolio_result: Any

    def summary(self) -> dict[str, Any]:
        return {
            **self.resolved.summary(),
            "portfolio_uid": self.portfolio_uid,
            "portfolio_unique_identifier": self.portfolio_unique_identifier,
            "interpolation": _summarize_run_result(self.interpolation_result),
            "calendar_events": _summarize_run_result(self.calendar_events_result),
            "rebalance": _summarize_run_result(self.rebalance_result),
            "portfolio_weights": _summarize_run_result(self.portfolio_weights_result),
            "portfolio": _summarize_run_result(self.portfolio_result),
        }


@dataclass(frozen=True, slots=True)
class PortfolioExecutionGraph:
    valuation_source: Any
    calendar_events: Any
    portfolio_rebalance: Any
    portfolio_weights: Any
    portfolio_node: Any
    portfolio_row: Any


def portfolio_unique_identifier(configuration_uid: Any) -> str:
    return f"ALPACA_ETF_PORTFOLIO__{str(configuration_uid).replace('-', '').upper()}"


def resolve_portfolio_configuration(
    configuration_uid: Any,
) -> ResolvedPortfolioConfiguration:
    """Resolve stored calculation inputs and require an existing signal observation."""
    from src.operations import signal_uid_for_configuration

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    signal_configuration, bars_configuration, rebalance_configuration = (
        validate_portfolio_references(
            signal_configuration_uid=configuration.signal_configuration_uid,
            bars_configuration_uid=configuration.bars_configuration_uid,
            rebalance_configuration_uid=configuration.rebalance_configuration_uid,
        )
    )
    signal_uid = signal_uid_for_configuration(signal_configuration)
    latest = read_signal_observation_matrix(signal_uid=signal_uid, observation_limit=1)
    bounds = read_signal_observation_bounds(signal_uid=signal_uid)
    if (
        not latest.time_indexes
        or not latest.assets
        or bounds.first_observation_at is None
        or bounds.last_observation_at is None
    ):
        raise ValueError(
            f"Signal {signal_uid} has no published observations. Run its Signal Job before "
            "running this portfolio."
        )
    asset_identifiers = tuple(sorted({asset.asset_identifier for asset in latest.assets}))
    source_uid = resolve_alpaca_bars_time_index_meta_table_uid(
        frequency_id=bars_configuration.frequency_id,
        feed=bars_configuration.feed,
        adjustment=bars_configuration.adjustment,
    )
    return ResolvedPortfolioConfiguration(
        configuration=configuration,
        signal_configuration=signal_configuration,
        bars_configuration=bars_configuration,
        rebalance_configuration=rebalance_configuration,
        signal_uid=signal_uid,
        signal_start_time=bounds.first_observation_at,
        asset_identifiers=asset_identifiers,
        source_time_index_meta_table_uid=source_uid,
    )


def build_portfolio_graph(resolved: ResolvedPortfolioConfiguration) -> PortfolioExecutionGraph:
    """Build the released ms-markets temporal portfolio graph."""
    from msm.api.portfolios import Portfolio
    from msm_portfolios.configuration import (
        BacktestingWeightsConfig,
        FrontEndDetails,
        PortfolioBuildConfiguration,
        PortfolioConfiguration,
        PortfolioExecutionConfiguration,
        PortfolioMarketsConfig,
        ValuationAlignmentPolicy,
    )
    from msm_portfolios.data_nodes import (
        PortfolioCalendarEvents,
        PortfolioCalendarEventsConfiguration,
        PortfoliosDataNode,
    )
    from msm_portfolios.rebalance_strategy import CalendarEventSignal

    configuration = resolved.configuration
    rebalance = resolved.rebalance_configuration
    if rebalance.strategy != "calendar_event_signal":
        raise ValueError("Portfolio execution requires CalendarEventSignal.")
    signal = build_alpaca_etf_holdings_signal(
        universe_uid=str(resolved.signal_configuration.universe_uid),
        account_uid=str(resolved.signal_configuration.account_uid),
    ).set_runtime_asset_list(list(resolved.asset_identifiers))
    if signal.signal_uid != resolved.signal_uid:
        raise RuntimeError("Resolved Signal identity changed while building the portfolio graph.")
    valuation_source = build_alpaca_interpolated_prices(
        source_time_index_meta_table_uid=resolved.source_time_index_meta_table_uid,
        asset_identifiers=list(resolved.asset_identifiers),
        calendar_identifier=rebalance.calendar_identifier,
        upsample_frequency_id=configuration.upsample_frequency_id,
        intraday_bar_interpolation_rule=configuration.intraday_bar_interpolation_rule,
    )
    backtest_days = max((dt.date.today() - resolved.signal_start_time.date()).days, 0)
    calendar_row = ensure_trading_calendar(
        rebalance.calendar_identifier,
        backtest_start_days=backtest_days,
    )
    if str(calendar_row.unique_identifier) != rebalance.calendar_identifier:
        raise RuntimeError(
            "The materialized Portfolio calendar does not match the rebalance configuration."
        )
    calendar_events = PortfolioCalendarEvents(
        config=PortfolioCalendarEventsConfiguration(
            offset_start=resolved.signal_start_time,
            calendar_identifier=rebalance.calendar_identifier,
            session_label=rebalance.session_label,
            event_types=(rebalance.rebalance_event,),
        )
    )
    description = configuration.description or (
        "Analytical ETF portfolio using already-published observed holdings weights and "
        "persistent interpolated Alpaca bars. Signal timestamps are observation times and do "
        "not guarantee the weights' exact economic effective time."
    )
    portfolio_configuration = PortfolioConfiguration(
        portfolio_build_configuration=PortfolioBuildConfiguration(
            valuation_source_instance=valuation_source,
            valuation_column=configuration.valuation_column,
            valuation_alignment_policy=ValuationAlignmentPolicy(
                maximum_staleness=dt.timedelta(
                    seconds=configuration.valuation_maximum_staleness_seconds
                ),
                fail_on_missing_values=configuration.fail_on_missing_prices,
            ),
            execution_configuration=PortfolioExecutionConfiguration(
                commission_fee=configuration.commission_fee
            ),
            backtesting_weights_configuration=BacktestingWeightsConfig(
                rebalance_strategy_instance=CalendarEventSignal(
                    calendar_events_instance=calendar_events,
                    calendar_identifier=rebalance.calendar_identifier,
                    session_label=rebalance.session_label,
                    rebalance_event=rebalance.rebalance_event,
                    event_offset=dt.timedelta(seconds=rebalance.event_offset_seconds),
                    rebalance_cadence=rebalance.rebalance_cadence,
                    rebalance_weekday=rebalance.rebalance_weekday,
                ),
                signal_weights_instance=signal,
            ),
        ),
        portfolio_markets_configuration=PortfolioMarketsConfig(
            portfolio_name=configuration.name,
            front_end_details=FrontEndDetails(
                description=description,
                signal_name=str(resolved.signal_configuration.name),
                signal_description=(
                    "Observed ETF component weights. Observation timestamps do not guarantee "
                    "exact economic effective times."
                ),
                rebalance_strategy_name="CalendarEventSignal",
                rebalance_strategy_description=(
                    "Select the latest signal observed at or before the configured persisted "
                    f"{rebalance.calendar_identifier} {rebalance.rebalance_event} event."
                ),
            ),
        ),
    )
    unique_identifier = portfolio_unique_identifier(configuration.uid)
    portfolio_row = Portfolio.upsert(
        unique_identifier=unique_identifier,
        calendar_uid=calendar_row.uid,
    )
    portfolio_node = PortfoliosDataNode(
        portfolio_configuration=portfolio_configuration,
        portfolio_description=description,
    )
    portfolio_node.target_portfolio = portfolio_row
    portfolio_node._explicit_portfolio_identifier = unique_identifier
    # An observation-time signal cannot support a portfolio before its first stored
    # observation. Keep this runtime calculation boundary out of durable portfolio identity.
    portfolio_node.OFFSET_START = resolved.signal_start_time
    portfolio_weights = portfolio_node.dependencies()["portfolio_weights"]
    portfolio_rebalance = portfolio_weights.portfolio_rebalance
    if portfolio_rebalance is None:
        raise RuntimeError("ms-markets did not construct the PortfolioRebalance dependency.")
    portfolio_rebalance.OFFSET_START = resolved.signal_start_time
    portfolio_weights.OFFSET_START = resolved.signal_start_time
    return PortfolioExecutionGraph(
        valuation_source=valuation_source,
        calendar_events=calendar_events,
        portfolio_rebalance=portfolio_rebalance,
        portfolio_weights=portfolio_weights,
        portfolio_node=portfolio_node,
        portfolio_row=portfolio_row,
    )


def execute_portfolio_configuration(configuration_uid: Any) -> PortfolioExecutionResult:
    """Run stored inputs through the released temporal execution stages.

    The linked Signal Job remains the signal producer. This execution updates every other
    dependency explicitly so it does not trigger another ETF extraction as a side effect.
    """
    from src.runtime import start_portfolio_job_engine

    # This service is reusable outside the repository Job entrypoint. Bootstrap the complete
    # portfolio model set before configuration readers can initialize a smaller app runtime.
    start_portfolio_job_engine()
    resolved = resolve_portfolio_configuration(configuration_uid)
    graph = build_portfolio_graph(resolved)
    interpolation_result = graph.valuation_source.run(update_tree=False)
    calendar_events_result = graph.calendar_events.run(update_tree=False)
    signal_weights = graph.portfolio_node.signal_weights
    signal_statistics = signal_weights.get_update_statistics()
    signal_statistics.filter_identity_level(level=1, filters=[resolved.signal_uid])
    signal_weights.update_statistics = signal_statistics
    rebalance_result = graph.portfolio_rebalance.run(update_tree=False)
    portfolio_weights_result = graph.portfolio_weights.run(update_tree=False)
    portfolio_result = graph.portfolio_node.run(update_tree=False, update_pointers=True)
    persisted_portfolio = (
        getattr(graph.portfolio_node, "target_portfolio", None) or graph.portfolio_row
    )
    portfolio_uid = str(persisted_portfolio.uid)
    if (
        resolved.configuration.portfolio_uid is None
        or str(resolved.configuration.portfolio_uid) != portfolio_uid
    ):
        update_portfolio_configuration_row(
            resolved.configuration.uid,
            values={"portfolio_uid": uuid.UUID(portfolio_uid)},
        )
    return PortfolioExecutionResult(
        resolved=resolved,
        portfolio_uid=portfolio_uid,
        portfolio_unique_identifier=str(persisted_portfolio.unique_identifier),
        interpolation_result=interpolation_result,
        calendar_events_result=calendar_events_result,
        rebalance_result=rebalance_result,
        portfolio_weights_result=portfolio_weights_result,
        portfolio_result=portfolio_result,
    )


def _summarize_run_result(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _summarize_run_result(item) for key, item in value.items()}
    if isinstance(value, tuple) and len(value) == 2:
        error, frame = value
        if error:
            raise RuntimeError("Portfolio pipeline update failed; see DataNode logs.")
        return {"error": False, "rows": int(getattr(frame, "shape", (0,))[0])}
    uid = getattr(value, "uid", None)
    if uid is not None:
        return {"uid": str(uid)}
    return str(value)


__all__ = [
    "PortfolioExecutionResult",
    "PortfolioExecutionGraph",
    "ResolvedPortfolioConfiguration",
    "build_portfolio_graph",
    "execute_portfolio_configuration",
    "portfolio_unique_identifier",
    "resolve_portfolio_configuration",
]
