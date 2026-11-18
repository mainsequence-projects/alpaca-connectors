"""Resolve and run durable portfolio configurations.

Every portfolio belongs to one account and follows the ``ApplicationWeights`` signal of one of its
account's signal definitions, the weights the application provides (ADR 0024, ADR 0027, ADR 0028).
A portfolio not yet adopted runs as it did before ADR 0028: on its own signal and the bars feed it
names.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from alpaca_connectors.assets.calendars import ensure_trading_calendar
from alpaca_connectors.portfolios.configurations import (
    AlpacaPortfolioConfiguration,
    PortfolioRebalanceConfiguration,
    get_portfolio_configuration,
    update_portfolio_configuration_row,
    validate_portfolio_references,
)
from alpaca_connectors.portfolios.interpolated_prices import (
    build_alpaca_interpolated_prices,
    resolve_alpaca_bars_time_index_meta_table_uid,
)
from alpaca_connectors.portfolios.rebalance_schedule import first_rebalance_state
from alpaca_connectors.portfolios.signal_history import (
    read_signal_observation_bounds,
    read_signal_observation_matrix,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedPortfolioConfiguration:
    configuration: AlpacaPortfolioConfiguration
    bars_configuration: Any
    rebalance_configuration: PortfolioRebalanceConfiguration
    signal_uid: str
    signal_start_time: dt.datetime
    asset_identifiers: tuple[str, ...]
    source_time_index_meta_table_uid: str

    def summary(self) -> dict[str, Any]:
        return {
            "configuration_uid": str(self.configuration.uid),
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
    bars_update: dict[str, Any] = field(default_factory=dict)
    first_rebalance: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            **self.resolved.summary(),
            "portfolio_uid": self.portfolio_uid,
            "portfolio_unique_identifier": self.portfolio_unique_identifier,
            "bars_update": dict(self.bars_update),
            "first_rebalance": dict(self.first_rebalance),
            "interpolation": _summarize_run_result(self.interpolation_result),
            "calendar_events": _summarize_run_result(self.calendar_events_result),
            "rebalance": _summarize_run_result(self.rebalance_result),
            "portfolio_weights": _summarize_run_result(self.portfolio_weights_result),
            "portfolio": _summarize_run_result(self.portfolio_result),
        }


@dataclass(frozen=True, slots=True)
class PortfolioPreflight:
    """Whether a portfolio configuration's stored inputs resolve for a run."""

    configuration_uid: str
    blockers: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return not self.blockers


@dataclass(frozen=True, slots=True)
class PortfolioExecutionGraph:
    valuation_source: Any
    # None for immediate_signal, which trades at the signal's own times and reads no calendar.
    calendar_events: Any
    portfolio_rebalance: Any
    portfolio_weights: Any
    portfolio_node: Any
    portfolio_row: Any


def portfolio_unique_identifier(configuration_uid: Any) -> str:
    """The `Portfolio` unique identifier of a configuration (ADR 0024, decision 3)."""
    from alpaca_connectors.portfolios.provided_weights import provided_portfolio_unique_identifier

    return provided_portfolio_unique_identifier(configuration_uid)


def resolve_portfolio_configuration(
    configuration_uid: Any,
) -> ResolvedPortfolioConfiguration:
    """Resolve stored calculation inputs and require an existing signal observation."""
    from alpaca_connectors.market_data.configurations import (
        get_bar_configuration,
        portfolio_bar_configuration,
    )
    from alpaca_connectors.portfolios.provided_weights import portfolio_signal_uid
    from alpaca_connectors.portfolios.signal_assets import assets_for_signals

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    rebalance_configuration = validate_portfolio_references(
        rebalance_configuration_uid=configuration.rebalance_configuration_uid,
    )
    bars_configuration = portfolio_bar_configuration(configuration.uid)
    if bars_configuration is None and configuration.bars_configuration_uid is not None:
        # A portfolio not yet adopted is priced by the bars feed it names, as before ADR 0028.
        bars_configuration = get_bar_configuration(configuration.bars_configuration_uid)
    if bars_configuration is None:
        raise LookupError(
            f"Portfolio configuration {configuration_uid!s} has no bars configuration."
        )
    if not bars_configuration.enabled:
        raise ValueError(f"Bars configuration {bars_configuration.uid!s} is disabled.")
    signal_uid = portfolio_signal_uid(configuration)
    latest = read_signal_observation_matrix(signal_uid=signal_uid, observation_limit=1)
    bounds = read_signal_observation_bounds(signal_uid=signal_uid)
    if (
        not latest.time_indexes
        or not latest.assets
        or bounds.first_observation_at is None
        or bounds.last_observation_at is None
    ):
        raise ValueError(
            f"Signal {signal_uid} has no published observations. "
            "Publish weights to the portfolio's signal before running it."
        )
    # Every asset the portfolio ever held, so a history it started with is priced too; read from
    # the signal-asset table, not the signal weights series (ADR 0024).
    asset_identifiers = tuple(
        sorted(
            {asset.asset_identifier for asset in latest.assets}
            | set(assets_for_signals([signal_uid]))
        )
    )
    source_uid = resolve_alpaca_bars_time_index_meta_table_uid(
        frequency_id=bars_configuration.frequency_id,
        feed=bars_configuration.feed,
        adjustment=bars_configuration.adjustment,
    )
    return ResolvedPortfolioConfiguration(
        configuration=configuration,
        bars_configuration=bars_configuration,
        rebalance_configuration=rebalance_configuration,
        signal_uid=signal_uid,
        signal_start_time=bounds.first_observation_at,
        asset_identifiers=asset_identifiers,
        source_time_index_meta_table_uid=source_uid,
    )


def preflight_portfolio_configuration(configuration_uid: Any) -> PortfolioPreflight:
    """Resolve the inputs a run needs, read-only, and report what blocks the run.

    A linked configuration that is missing or disabled, an unsupported rebalance strategy, or a
    signal without published observations blocks the run. Any other failure propagates.
    """
    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    try:
        resolve_portfolio_configuration(configuration.uid)
    except (LookupError, ValueError) as exc:
        return PortfolioPreflight(configuration_uid=str(configuration.uid), blockers=(str(exc),))
    return PortfolioPreflight(configuration_uid=str(configuration.uid), blockers=())


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
    from msm_portfolios.rebalance_strategy import CalendarEventSignal, ImmediateSignal
    from msm_portfolios.time_index import (
        PortfolioCalendarEvents,
        PortfolioCalendarEventsConfiguration,
        PortfoliosUpdater,
    )

    from alpaca_connectors.instances import (
        AlpacaPortfolioCalendarEventsStorage,
        portfolio_output_tables,
    )
    from alpaca_connectors.portfolios.provided_weights import portfolio_signal_key
    from alpaca_connectors.portfolios.signal_definitions import weights_signal

    configuration = resolved.configuration
    rebalance = resolved.rebalance_configuration
    if rebalance.strategy not in ("calendar_event_signal", "immediate_signal"):
        raise ValueError("Portfolio execution requires CalendarEventSignal or ImmediateSignal.")
    signal = weights_signal(portfolio_signal_key(configuration))
    # `ApplicationWeights.get_asset_list()` reads the whole signal weights table; the resolved
    # scope is the same set, from the signal-asset table. The class path, and so the UID, stay.
    recorded_assets = list(resolved.asset_identifiers)
    signal.get_asset_list = lambda: list(recorded_assets)
    if signal.signal_uid != resolved.signal_uid:
        raise RuntimeError("Resolved Signal identity changed while building the portfolio graph.")
    valuation_source = build_alpaca_interpolated_prices(
        source_time_index_meta_table_uid=resolved.source_time_index_meta_table_uid,
        asset_identifiers=list(resolved.asset_identifiers),
        upsample_frequency_id=configuration.upsample_frequency_id,
        intraday_bar_interpolation_rule=configuration.intraday_bar_interpolation_rule,
    )
    calendar_row = ensure_trading_calendar(rebalance.calendar_identifier)
    if str(calendar_row.unique_identifier) != rebalance.calendar_identifier:
        raise RuntimeError(
            "The stored Portfolio calendar does not match the rebalance configuration."
        )
    if rebalance.strategy == "immediate_signal":
        # Each set of weights is traded at its own observation time (ms-markets ImmediateSignal);
        # no calendar event is read. The calendar above is the `Portfolio` row's.
        calendar_events = None
        rebalance_strategy = ImmediateSignal()
        rebalance_strategy_description = (
            "Trade to each set of weights the application provides, at the time it was observed."
        )
    else:
        calendar_events = PortfolioCalendarEvents(
            config=PortfolioCalendarEventsConfiguration(
                offset_start=resolved.signal_start_time,
                calendar_identifier=rebalance.calendar_identifier,
                session_label=rebalance.session_label,
                event_types=(rebalance.rebalance_event,),
            ),
            output_table=AlpacaPortfolioCalendarEventsStorage,
        )
        rebalance_strategy = CalendarEventSignal(
            calendar_events_instance=calendar_events,
            calendar_identifier=rebalance.calendar_identifier,
            session_label=rebalance.session_label,
            rebalance_event=rebalance.rebalance_event,
            event_offset=dt.timedelta(seconds=rebalance.event_offset_seconds),
            rebalance_cadence=rebalance.rebalance_cadence,
            rebalance_weekday=rebalance.rebalance_weekday,
        )
        rebalance_strategy_description = (
            "Select the latest signal observed at or before the configured persisted "
            f"{rebalance.calendar_identifier} {rebalance.rebalance_event} event."
        )
    description = configuration.description or (
        "Analytical portfolio following weights its owner application provides, valued with "
        "persistent interpolated Alpaca bars."
    )
    signal_name = f"{configuration.name} provided weights"
    signal_description = (
        "Weights an application provides. Each observation is the whole set; an asset it leaves "
        "out is held at zero."
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
                rebalance_strategy_instance=rebalance_strategy,
                signal_weights_instance=signal,
            ),
        ),
        portfolio_markets_configuration=PortfolioMarketsConfig(
            portfolio_name=configuration.name,
            front_end_details=FrontEndDetails(
                description=description,
                signal_name=signal_name,
                signal_description=signal_description,
                rebalance_strategy_name=type(rebalance_strategy).__name__,
                rebalance_strategy_description=rebalance_strategy_description,
            ),
        ),
    )
    unique_identifier = portfolio_unique_identifier(configuration.uid)
    portfolio_row = Portfolio.upsert(
        unique_identifier=unique_identifier,
        calendar_uid=calendar_row.uid,
    )
    # Values, weights and rebalance state land in this application's contract instances
    # (ms-markets ADR 0047); output tables do not enter any update hash.
    portfolio_node = PortfoliosUpdater(
        portfolio_configuration=portfolio_configuration,
        portfolio_description=description,
        **portfolio_output_tables(),
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


def update_portfolio_bars(resolved: ResolvedPortfolioConfiguration) -> dict[str, Any]:
    """Fetch the bars the run values with, before it values (issue #39).

    Without it, a run started at once after new weights lacks the prices of an asset the signal
    just gained until the 00:00 UTC bars run, and fails on missing prices. This is the bars Job's
    own update for the run's bars configuration (the portfolio's own, or the feed a portfolio not
    yet adopted names), which prices every asset the signal has published, incrementally. A
    failed update (keys removed, Alpaca unavailable) never stops the run: it values with the
    prices already stored, as it did before, and the summary says the update failed.
    """
    from alpaca_connectors.market_data import execute_market_data_update

    bars_uid = str(resolved.bars_configuration.uid)
    try:
        result = execute_market_data_update(configuration_uid=bars_uid)
    except Exception as exc:  # noqa: BLE001 - the run keeps what worked before (issue #39)
        # The type only: a provider message can carry request context.
        logger.warning(
            "The portfolio's bars update failed (%s); the run values with the stored prices.",
            type(exc).__name__,
        )
        return {"bars_configuration_uid": bars_uid, "updated": False, "error": type(exc).__name__}
    return {
        "bars_configuration_uid": bars_uid,
        "updated": True,
        "rows_persisted": int(result.get("rows_persisted") or 0),
    }


def execute_portfolio_configuration(configuration_uid: Any) -> PortfolioExecutionResult:
    """Run stored inputs through the released temporal execution stages.

    The calls that give the portfolio weights remain the signal producer; this execution updates
    every other dependency explicitly and publishes no signal observation.
    """
    from alpaca_connectors.runtime import start_portfolio_job_engine

    # This service is reusable outside the repository Job entrypoint. Bootstrap the complete
    # portfolio model set before configuration readers can initialize a smaller app runtime.
    start_portfolio_job_engine()
    resolved = resolve_portfolio_configuration(configuration_uid)
    bars_update = update_portfolio_bars(resolved)
    graph = build_portfolio_graph(resolved)
    interpolation_result = graph.valuation_source.run(update_tree=False)
    calendar_events_result = (
        None if graph.calendar_events is None else graph.calendar_events.run(update_tree=False)
    )
    signal_weights = graph.portfolio_node.signal_weights
    signal_statistics = signal_weights.get_update_statistics()
    signal_statistics.filter_identity_level(level=1, filters=[resolved.signal_uid])
    signal_weights.update_statistics = signal_statistics
    # The rebalance reads the events up to its own start, so it reaches at least this time.
    rebalanced_through = dt.datetime.now(dt.UTC)
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
    # Point the Markets dataset catalog at this application's instances and refresh its state.
    from alpaca_connectors.portfolios.datasets import register_portfolio_datasets

    register_portfolio_datasets(portfolio_uid)
    first_rebalance = first_rebalance_state(
        resolved.rebalance_configuration,
        starts_at=resolved.signal_start_time,
        at=rebalanced_through,
    )
    if not first_rebalance["rebalanced"]:
        # Issue #41: a run before the first configured event writes no weights.
        logger.warning("%s", first_rebalance["message"])
    return PortfolioExecutionResult(
        resolved=resolved,
        portfolio_uid=portfolio_uid,
        portfolio_unique_identifier=str(persisted_portfolio.unique_identifier),
        interpolation_result=interpolation_result,
        calendar_events_result=calendar_events_result,
        rebalance_result=rebalance_result,
        portfolio_weights_result=portfolio_weights_result,
        portfolio_result=portfolio_result,
        bars_update=bars_update,
        first_rebalance=first_rebalance,
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
    "PortfolioPreflight",
    "ResolvedPortfolioConfiguration",
    "build_portfolio_graph",
    "execute_portfolio_configuration",
    "portfolio_unique_identifier",
    "preflight_portfolio_configuration",
    "resolve_portfolio_configuration",
]
