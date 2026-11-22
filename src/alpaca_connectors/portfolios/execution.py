"""Resolve and run durable portfolio configurations.

Every portfolio belongs to one account and follows the ``ApplicationWeights`` signal of one of its
account's signal definitions, the weights the application provides (ADR 0024, ADR 0027, ADR 0028).
A portfolio not yet adopted runs as it did before ADR 0028: on its own signal and the bars feed it
names. A run is the ms-markets recipe ``run_portfolio``, which publishes the portfolio's weights and
values to Markets (ADR 0031); this application supplies its prices, its private rebalance-state and
calendar-events tables, and the configuration.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpaca_connectors.assets.calendars import ensure_trading_calendar
from alpaca_connectors.portfolios.configurations import (
    AlpacaPortfolioConfiguration,
    PortfolioRebalanceConfiguration,
    get_portfolio_configuration,
    update_portfolio_configuration_row,
)
from alpaca_connectors.portfolios.interpolated_prices import (
    build_alpaca_interpolated_prices,
    resolve_alpaca_bars_time_index_meta_table_uids,
)
from alpaca_connectors.portfolios.rebalance_schedule import first_rebalance_state
from alpaca_connectors.portfolios.signal_observations import read_signal_observation_matrix

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
    # Rows the run published per series, ``weights`` and ``values``, as Markets answered
    # (``markets_api.run_portfolio``). The recipe does not count the rows of the private stages.
    written: dict[str, int] = field(default_factory=dict)
    bars_update: dict[str, Any] = field(default_factory=dict)
    first_rebalance: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        def stage(name: str) -> dict[str, Any] | None:
            if name not in self.written:
                return None
            return {"error": False, "rows": int(self.written[name])}

        return {
            **self.resolved.summary(),
            "portfolio_uid": self.portfolio_uid,
            "portfolio_unique_identifier": self.portfolio_unique_identifier,
            "bars_update": dict(self.bars_update),
            "first_rebalance": dict(self.first_rebalance),
            "interpolation": None,
            "calendar_events": None,
            "rebalance": None,
            "portfolio_weights": stage("weights"),
            "portfolio": stage("values"),
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
class PortfolioRunPlan:
    """What this application supplies to the ms-markets recipe ``run_portfolio`` (ADR 0031).

    The valuation source, its interpolated prices, is in ``portfolio_configuration``.
    """

    # The calendar's session events, private to this application; the strategy reads them.
    calendar_events: Any
    portfolio_configuration: Any
    portfolio_description: str
    unique_identifier: str
    calendar_uid: Any


def portfolio_unique_identifier(configuration_uid: Any) -> str:
    """The `Portfolio` unique identifier of a configuration (ADR 0024, decision 3)."""
    from alpaca_connectors.portfolios.provided_weights import provided_portfolio_unique_identifier

    return provided_portfolio_unique_identifier(configuration_uid)


@dataclass(frozen=True, slots=True)
class HeldPortfolioInputs:
    """What a caller already holds of the inputs a preflight reads, so they are not read again.

    A portfolio create passes what it just wrote (issue #46). Keys are UID strings: rebalance
    configurations and definitions by their own UID, bars configurations by the portfolio
    configuration UID they price, bounds by signal UID.
    """

    rebalance_configurations: Mapping[str, Any] = field(default_factory=dict)
    bars_configurations: Mapping[str, Any] = field(default_factory=dict)
    definitions: Mapping[str, Any] = field(default_factory=dict)
    signal_bounds: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _PortfolioInputs:
    configuration: AlpacaPortfolioConfiguration
    rebalance_configuration: PortfolioRebalanceConfiguration
    bars_configuration: Any
    signal_uid: str
    signal_start_time: dt.datetime
    source_time_index_meta_table_uid: str


def _resolve_inputs(
    configurations: Sequence[AlpacaPortfolioConfiguration],
    *,
    held: HeldPortfolioInputs | None = None,
) -> dict[str, _PortfolioInputs | Exception]:
    """Resolve the stored inputs of several configurations, with one read per kind.

    The rebalance configurations, the portfolios' own bars configurations, the feeds the
    portfolios not yet adopted name, the signal definitions, the signals' observation bounds and
    the bars tables are each read once for all of them (issue #46); what ``held`` gives is not
    read. Each configuration is answered with its inputs or with the error that blocks it: a
    ``LookupError`` or ``ValueError`` for an input that is missing, disabled or unsupported, a
    ``RuntimeError`` for a bars table that is not registered.
    """
    from alpaca_connectors.market_data.configurations import (
        bar_configuration_rows_by_uids,
        portfolio_bar_configurations,
    )
    from alpaca_connectors.portfolios.configurations import (
        normalize_rebalance_strategy,
        rebalance_configurations_by_uids,
    )
    from alpaca_connectors.portfolios.signal_definitions import (
        legacy_signal_key,
        signal_definitions_by_uids,
        signal_uid_for_key,
    )
    from alpaca_connectors.portfolios.signal_observations import (
        read_signal_observation_bounds_by_signal,
    )

    held = held or HeldPortfolioInputs()
    uids = [str(configuration.uid) for configuration in configurations]

    rebalances = dict(held.rebalance_configurations)
    rebalances.update(
        rebalance_configurations_by_uids(
            [
                str(configuration.rebalance_configuration_uid)
                for configuration in configurations
                if str(configuration.rebalance_configuration_uid) not in rebalances
            ]
        )
    )
    own_bars = dict(held.bars_configurations)
    own_bars.update(portfolio_bar_configurations([uid for uid in uids if uid not in own_bars]))
    # A portfolio not yet adopted is priced by the bars feed it names, as before ADR 0028.
    named_bars = bar_configuration_rows_by_uids(
        [
            configuration.bars_configuration_uid
            for configuration in configurations
            if str(configuration.uid) not in own_bars
            and configuration.bars_configuration_uid is not None
        ]
    )
    definitions = dict(held.definitions)
    definitions.update(
        signal_definitions_by_uids(
            configuration.signal_definition_uid
            for configuration in configurations
            if configuration.signal_definition_uid is not None
            and str(configuration.signal_definition_uid) not in definitions
        )
    )

    resolved: dict[str, Any] = {}
    for configuration in configurations:
        uid = str(configuration.uid)
        try:
            rebalance = rebalances.get(str(configuration.rebalance_configuration_uid))
            if rebalance is None:
                raise LookupError(
                    f"Rebalance configuration {configuration.rebalance_configuration_uid!s} "
                    "does not exist."
                )
            normalize_rebalance_strategy(rebalance.strategy)
            bars = own_bars.get(uid)
            if bars is None and configuration.bars_configuration_uid is not None:
                bars = named_bars.get(str(configuration.bars_configuration_uid))
            if bars is None:
                raise LookupError(f"Portfolio configuration {uid} has no bars configuration.")
            if not bars.enabled:
                raise ValueError(f"Bars configuration {bars.uid!s} is disabled.")
            if configuration.signal_definition_uid is None:
                signal_key = legacy_signal_key(configuration.uid)
            else:
                definition = definitions.get(str(configuration.signal_definition_uid))
                if definition is None:
                    raise LookupError(
                        f"Signal definition {configuration.signal_definition_uid!s} does not exist."
                    )
                signal_key = definition.signal_key
        except (LookupError, ValueError) as exc:
            resolved[uid] = exc
            continue
        resolved[uid] = (configuration, rebalance, bars, signal_uid_for_key(signal_key))

    pending = {uid: value for uid, value in resolved.items() if isinstance(value, tuple)}
    bounds = dict(held.signal_bounds)
    bounds.update(
        read_signal_observation_bounds_by_signal(
            [value[3] for value in pending.values() if value[3] not in bounds]
        )
    )
    sources = (
        resolve_alpaca_bars_time_index_meta_table_uids(
            [
                (value[2].frequency_id, value[2].feed, value[2].adjustment)
                for value in pending.values()
            ]
        )
        if pending
        else {}
    )
    for uid, (configuration, rebalance, bars, signal_uid) in pending.items():
        signal_bounds = bounds.get(signal_uid)
        if (
            signal_bounds is None
            or signal_bounds.first_observation_at is None
            or signal_bounds.last_observation_at is None
        ):
            resolved[uid] = ValueError(
                f"Signal {signal_uid} has no published observations. "
                "Publish weights to the portfolio's signal before running it."
            )
            continue
        source = sources[(bars.frequency_id, bars.feed, bars.adjustment)]
        if isinstance(source, Exception):
            resolved[uid] = source
            continue
        resolved[uid] = _PortfolioInputs(
            configuration=configuration,
            rebalance_configuration=rebalance,
            bars_configuration=bars,
            signal_uid=signal_uid,
            signal_start_time=signal_bounds.first_observation_at,
            source_time_index_meta_table_uid=source,
        )
    return resolved


def resolve_portfolio_configuration(
    configuration_uid: Any,
) -> ResolvedPortfolioConfiguration:
    """Resolve stored calculation inputs and require an existing signal observation."""
    from alpaca_connectors.portfolios.signal_assets import assets_for_signals

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    inputs = _resolve_inputs([configuration])[str(configuration.uid)]
    if isinstance(inputs, Exception):
        raise inputs
    latest = read_signal_observation_matrix(signal_uid=inputs.signal_uid, observation_limit=1)
    if not latest.time_indexes or not latest.assets:
        raise ValueError(
            f"Signal {inputs.signal_uid} has no published observations. "
            "Publish weights to the portfolio's signal before running it."
        )
    # Every asset the portfolio ever held, so a history it started with is priced too; read from
    # the signal-asset table, not the signal weights series (ADR 0024).
    asset_identifiers = tuple(
        sorted(
            {asset.asset_identifier for asset in latest.assets}
            | set(assets_for_signals([inputs.signal_uid]))
        )
    )
    return ResolvedPortfolioConfiguration(
        configuration=configuration,
        bars_configuration=inputs.bars_configuration,
        rebalance_configuration=inputs.rebalance_configuration,
        signal_uid=inputs.signal_uid,
        signal_start_time=inputs.signal_start_time,
        asset_identifiers=asset_identifiers,
        source_time_index_meta_table_uid=inputs.source_time_index_meta_table_uid,
    )


def preflight_portfolio_configurations(
    configurations: Sequence[AlpacaPortfolioConfiguration],
    *,
    held: HeldPortfolioInputs | None = None,
) -> dict[str, PortfolioPreflight | Exception]:
    """The preflight of several configurations, with one read per kind of input (issue #46).

    Each configuration is answered with its preflight, whose blockers are a linked configuration
    that is missing or disabled, an unsupported rebalance strategy, or a signal without published
    observations; or with any other error its inputs raised, which blocks only it.
    """
    answers: dict[str, PortfolioPreflight | Exception] = {}
    for uid, inputs in _resolve_inputs(configurations, held=held).items():
        if isinstance(inputs, (LookupError, ValueError)):
            answers[uid] = PortfolioPreflight(configuration_uid=uid, blockers=(str(inputs),))
        elif isinstance(inputs, Exception):
            answers[uid] = inputs
        else:
            answers[uid] = PortfolioPreflight(configuration_uid=uid, blockers=())
    return answers


def preflight_portfolio_configuration(
    configuration_uid: Any,
    *,
    configuration: AlpacaPortfolioConfiguration | None = None,
    held: HeldPortfolioInputs | None = None,
) -> PortfolioPreflight:
    """Resolve the inputs a run needs, read-only, and report what blocks the run.

    A linked configuration that is missing or disabled, an unsupported rebalance strategy, or a
    signal without published observations blocks the run. Any other failure propagates. A caller
    that holds the configuration, or some of its inputs, passes them and they are not read.
    """
    if configuration is None:
        configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    answer = preflight_portfolio_configurations([configuration], held=held)[str(configuration.uid)]
    if isinstance(answer, Exception):
        raise answer
    return answer


def build_portfolio_run(resolved: ResolvedPortfolioConfiguration) -> PortfolioRunPlan:
    """Build what this application supplies to a portfolio run (ADR 0031, decision 5).

    Its interpolated prices as the valuation source, calendar events only for
    ``calendar_event_signal`` (in its private table), the rebalance strategy, the calendar and the
    ``ApplicationWeights`` signal of the configuration, the valuation and fee settings, and the
    ``ALPACA_PORTFOLIO__<hex>`` identifier. ms-markets' ``run_portfolio`` builds and runs the
    chain from it.
    """
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
    from msm_portfolios.rebalance_strategy.immediate_signal import SESSION_EVENT_TYPES
    from msm_portfolios.time_index import (
        PortfolioCalendarEvents,
        PortfolioCalendarEventsConfiguration,
    )

    from alpaca_connectors.holdings.account import DEFAULT_CASH_ASSET_IDENTIFIER
    from alpaca_connectors.instances import AlpacaPortfolioCalendarEventsStorage
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
        raise RuntimeError("Resolved Signal identity changed while building the portfolio run.")
    valuation_source = build_alpaca_interpolated_prices(
        source_time_index_meta_table_uid=resolved.source_time_index_meta_table_uid,
        asset_identifiers=list(resolved.asset_identifiers),
        profile=(
            resolved.bars_configuration.frequency_id,
            resolved.bars_configuration.feed,
            resolved.bars_configuration.adjustment,
        ),
        upsample_frequency_id=configuration.upsample_frequency_id,
        intraday_bar_interpolation_rule=configuration.intraday_bar_interpolation_rule,
    )
    calendar_row = ensure_trading_calendar(rebalance.calendar_identifier)
    if str(calendar_row.unique_identifier) != rebalance.calendar_identifier:
        raise RuntimeError(
            "The stored Portfolio calendar does not match the rebalance configuration."
        )
    # Both strategies read the sessions of the portfolio's calendar (ms-markets ADR 0058): a
    # trade waits for the market to be open, and each asset trades at its next observed price.
    # The events table publishes both session boundaries; `calendar_event_signal`'s own event is
    # one of them. They are this application's private rows.
    calendar_events = PortfolioCalendarEvents(
        config=PortfolioCalendarEventsConfiguration(
            offset_start=resolved.signal_start_time,
            calendar_identifier=rebalance.calendar_identifier,
            session_label=rebalance.session_label,
            event_types=SESSION_EVENT_TYPES,
        ),
        output_table=AlpacaPortfolioCalendarEventsStorage,
    )
    if rebalance.strategy == "immediate_signal":
        rebalance_strategy = ImmediateSignal(
            calendar_events_instance=calendar_events,
            calendar_identifier=rebalance.calendar_identifier,
            session_label=rebalance.session_label,
        )
        rebalance_strategy_description = (
            "Trade to each set of weights the application provides: from the time it was "
            f"observed while the {rebalance.calendar_identifier} market is open, otherwise from "
            "the next open, each asset at its next observed price."
        )
    else:
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
            f"{rebalance.calendar_identifier} {rebalance.rebalance_event} event; each asset "
            "trades at its next observed price from then, while the market is open."
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
            # The portfolio's currency (ms-markets ADR 0059): an Alpaca account's cash, US dollars.
            valuation_asset_identifier=DEFAULT_CASH_ASSET_IDENTIFIER,
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
    return PortfolioRunPlan(
        calendar_events=calendar_events,
        portfolio_configuration=portfolio_configuration,
        portfolio_description=description,
        unique_identifier=portfolio_unique_identifier(configuration.uid),
        calendar_uid=calendar_row.uid,
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
    """Run a portfolio configuration with the ms-markets recipe ``run_portfolio`` (ADR 0031).

    Its bars first (issue #39), then the run: ms-markets runs the calendar events (for
    ``calendar_event_signal``), the rebalance state into this application's private table, the
    executed weights and the interpolated prices, then the values, from the signal's first
    observation and reading only its one signal; the `Portfolio` row is the configuration's
    ``ALPACA_PORTFOLIO__<hex>``, and the weights and values are published to Markets. Nothing is
    registered or reconciled. The calls that give the portfolio weights remain the signal
    producer; this run publishes no signal observation.
    """
    from alpaca_connectors.instances import AlpacaPortfolioRebalanceStateStorage
    from alpaca_connectors.markets_api import portfolio_weights_recorded, run_portfolio
    from alpaca_connectors.runtime import start_portfolio_job_engine

    # This service is reusable outside the repository Job entrypoint. Bootstrap the complete
    # portfolio model set before configuration readers can initialize a smaller app runtime.
    start_portfolio_job_engine()
    resolved = resolve_portfolio_configuration(configuration_uid)
    bars_update = update_portfolio_bars(resolved)
    plan = build_portfolio_run(resolved)
    # The rebalance reads the events up to its own start, which is after this time.
    rebalanced_through = dt.datetime.now(dt.UTC)
    # ms-markets starts a first run at the signal's first observation (its portfolio_preflight),
    # the same time as ``resolved.signal_start_time``, which the calendar events start at too.
    answer = run_portfolio(
        plan.portfolio_configuration,
        unique_identifier=plan.unique_identifier,
        calendar_uid=plan.calendar_uid,
        portfolio_description=plan.portfolio_description,
        rebalance_output_table=AlpacaPortfolioRebalanceStateStorage,
    )
    portfolio_uid = answer.portfolio_uid
    if (
        resolved.configuration.portfolio_uid is None
        or str(resolved.configuration.portfolio_uid) != portfolio_uid
    ):
        update_portfolio_configuration_row(
            resolved.configuration.uid,
            values={"portfolio_uid": uuid.UUID(portfolio_uid)},
        )
    first_rebalance = first_rebalance_state(
        resolved.rebalance_configuration,
        starts_at=resolved.signal_start_time,
        at=rebalanced_through,
    )
    if first_rebalance["rebalanced"] and not portfolio_weights_recorded(
        answer.portfolio_unique_identifier
    ):
        # The first rebalance was due, and no run has recorded it: the run reports what Markets
        # holds, not what the schedule expected (ms-markets issue 118).
        first_rebalance = {
            **first_rebalance,
            "rebalanced": False,
            "message": (
                "The portfolio's first rebalance was due at "
                f"{first_rebalance['first_rebalance_at']}, and Markets holds no weights of it: "
                "no run has recorded that rebalance."
            ),
        }
    if not first_rebalance["rebalanced"]:
        # Issue #41: a run before the first configured event writes no weights.
        logger.warning("%s", first_rebalance["message"])
    return PortfolioExecutionResult(
        resolved=resolved,
        portfolio_uid=portfolio_uid,
        portfolio_unique_identifier=answer.portfolio_unique_identifier,
        written=dict(answer.written),
        bars_update=bars_update,
        first_rebalance=first_rebalance,
    )


__all__ = [
    "HeldPortfolioInputs",
    "PortfolioExecutionResult",
    "PortfolioRunPlan",
    "PortfolioPreflight",
    "ResolvedPortfolioConfiguration",
    "build_portfolio_run",
    "execute_portfolio_configuration",
    "portfolio_unique_identifier",
    "preflight_portfolio_configuration",
    "preflight_portfolio_configurations",
    "resolve_portfolio_configuration",
]
