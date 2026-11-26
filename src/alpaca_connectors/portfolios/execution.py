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
    build_account_interpolated_prices,
    resolve_alpaca_bars_tables,
)
from alpaca_connectors.portfolios.rebalance_schedule import (
    first_rebalance_state,
    recorded_rebalance_state,
)
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
    # The registered bars table the interpolated prices read (``resolve_alpaca_bars_tables``).
    source_table: Any = None

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


# The cash a portfolio's ledger opens with, in US dollars (ms-markets ADR 0065). It is the unit the
# rebalance sizes in; the published values are the NAV over it, so every portfolio starts at 1.
LEDGER_INITIAL_NAV = 1_000_000.0


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
    source_table: Any = None


def _resolve_inputs(
    configurations: Sequence[AlpacaPortfolioConfiguration],
    *,
    held: HeldPortfolioInputs | None = None,
) -> dict[str, _PortfolioInputs | Exception]:
    """Resolve the stored inputs of several configurations, with one read per kind.

    The rebalance configurations, the portfolios' own bars configurations, the feeds the
    portfolios not yet adopted name, the signal definitions, the signals' observation bounds and
    the bars tables are each read once for all of them (issue #46); what ``held`` gives is not
    read. The reads that need only the configurations run together, then the two that need what
    they answered (``all_together``). Each configuration is answered with its inputs or with the error that blocks it: a
    ``LookupError`` or ``ValueError`` for an input that is missing, disabled or unsupported, a
    ``RuntimeError`` for a bars table that is not registered.
    """
    from alpaca_connectors.market_data.configurations import (
        bar_configuration_rows_by_uids,
        portfolio_bar_configurations,
    )
    from alpaca_connectors.markets_api import all_together
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
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    held = held or HeldPortfolioInputs()
    uids = [str(configuration.uid) for configuration in configurations]
    # The reads below run together; the engine they share starts first, once.
    start_markets_engine(models=account_runtime_models())

    rebalances = dict(held.rebalance_configurations)
    own_bars = dict(held.bars_configurations)
    definitions = dict(held.definitions)
    read_rebalances, read_bars, read_definitions = all_together(
        lambda: rebalance_configurations_by_uids(
            [
                str(configuration.rebalance_configuration_uid)
                for configuration in configurations
                if str(configuration.rebalance_configuration_uid) not in rebalances
            ]
        ),
        lambda: portfolio_bar_configurations([uid for uid in uids if uid not in own_bars]),
        lambda: signal_definitions_by_uids(
            configuration.signal_definition_uid
            for configuration in configurations
            if configuration.signal_definition_uid is not None
            and str(configuration.signal_definition_uid) not in definitions
        ),
    )
    rebalances.update(read_rebalances)
    own_bars.update(read_bars)
    definitions.update(read_definitions)
    # A portfolio not yet adopted is priced by the bars feed it names, as before ADR 0028.
    named_bars = bar_configuration_rows_by_uids(
        [
            configuration.bars_configuration_uid
            for configuration in configurations
            if str(configuration.uid) not in own_bars
            and configuration.bars_configuration_uid is not None
        ]
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
    read_bounds, sources = all_together(
        lambda: read_signal_observation_bounds_by_signal(
            [value[3] for value in pending.values() if value[3] not in bounds]
        ),
        lambda: (
            resolve_alpaca_bars_tables(
                [
                    (value[2].frequency_id, value[2].feed, value[2].adjustment)
                    for value in pending.values()
                ]
            )
            if pending
            else {}
        ),
    )
    bounds.update(read_bounds)
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
            source_time_index_meta_table_uid=str(source.uid),
            source_table=source,
        )
    return resolved


def resolve_portfolio_configuration(
    configuration_uid: Any,
) -> ResolvedPortfolioConfiguration:
    """Resolve stored calculation inputs and require an existing signal observation.

    The latest observation and the signal's recorded assets are read together.
    """
    from alpaca_connectors.markets_api import all_together
    from alpaca_connectors.portfolios.signal_assets import assets_for_signals

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    inputs = _resolve_inputs([configuration])[str(configuration.uid)]
    if isinstance(inputs, Exception):
        raise inputs
    latest, recorded = all_together(
        lambda: read_signal_observation_matrix(signal_uid=inputs.signal_uid, observation_limit=1),
        lambda: assets_for_signals([inputs.signal_uid]),
    )
    if not latest.time_indexes or not latest.assets:
        raise ValueError(
            f"Signal {inputs.signal_uid} has no published observations. "
            "Publish weights to the portfolio's signal before running it."
        )
    # Every asset the portfolio ever held, so a history it started with is priced too; read from
    # the signal-asset table, not the signal weights series (ADR 0024).
    asset_identifiers = tuple(
        sorted({asset.asset_identifier for asset in latest.assets} | set(recorded))
    )
    return ResolvedPortfolioConfiguration(
        configuration=configuration,
        bars_configuration=inputs.bars_configuration,
        rebalance_configuration=inputs.rebalance_configuration,
        signal_uid=inputs.signal_uid,
        signal_start_time=inputs.signal_start_time,
        asset_identifiers=asset_identifiers,
        source_time_index_meta_table_uid=inputs.source_time_index_meta_table_uid,
        source_table=inputs.source_table,
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


def _bars_account_uid(resolved: ResolvedPortfolioConfiguration) -> str:
    """The account the portfolio's bars belong to: its feed's, or its own configuration's."""
    bars = resolved.bars_configuration
    account_uid = getattr(bars, "account_uid", None) or resolved.configuration.account_uid
    if account_uid is None:
        raise LookupError(
            f"Bar configuration {bars.uid!s} belongs to a portfolio without an account."
        )
    return str(account_uid)


def account_price_chain(
    resolved: ResolvedPortfolioConfiguration,
    chains: dict[tuple[Any, ...], Any] | None = None,
) -> Any:
    """The interpolated prices of the portfolio's account on its bars profile (ADR 0033).

    Their source is the account's ``AccountStockBars``. ``chains`` holds the ones a run has
    built: every portfolio of one account and profile gets the same instance, so they run once in
    the run's graph and the account's assets are resolved once in its process.
    """
    configuration = resolved.configuration
    bars = resolved.bars_configuration
    profile = (bars.frequency_id, bars.feed, bars.adjustment)
    key = (
        _bars_account_uid(resolved),
        profile,
        configuration.upsample_frequency_id,
        configuration.intraday_bar_interpolation_rule,
    )
    chains = {} if chains is None else chains
    if key not in chains:
        chains[key] = build_account_interpolated_prices(
            account_uid=key[0],
            profile=profile,
            upsample_frequency_id=configuration.upsample_frequency_id,
            intraday_bar_interpolation_rule=configuration.intraday_bar_interpolation_rule,
        )
    return chains[key]


def build_portfolio_run(
    resolved: ResolvedPortfolioConfiguration,
    *,
    price_chains: dict[tuple[Any, ...], Any] | None = None,
) -> PortfolioRunPlan:
    """Build what this application supplies to a portfolio run (ADR 0031, decision 5).

    Its account's interpolated prices on its bars profile as the valuation source
    (``account_price_chain``, ADR 0033), the calendar's session events (in its private table),
    the rebalance strategy with the configuration's commission, the calendar and the
    ``ApplicationWeights`` signal of the configuration, the ledger (ms-markets ADR 0065), and the
    ``ALPACA_PORTFOLIO__<hex>`` identifier. ms-markets' ``run_portfolio`` builds and runs the
    chain from it. A run of several portfolios passes one ``price_chains`` to every build.

    The ledger opens with ``LEDGER_INITIAL_NAV`` in cash at the close of the last NYSE session
    before the signal's first observation (``last_session_close_before``): its first rebalance
    sizes its shares from the last price observed before it, which must be in the ledger's window,
    so a ledger opening at the signal itself refuses that rebalance. It values every position at its interpolated close (``MarketPriceValuationModel``) with the
    configuration's staleness limit and missing-price rule. Its published values are the NAV over
    the initial NAV, so a portfolio starts at 1. The commission is a proportional cost of each
    fill's notional (``ProportionalExecutionCostModel``).
    """
    from msm_portfolios.accounting import (
        MarketPriceValuationModel,
        PortfolioAccountingConfiguration,
    )
    from msm_portfolios.configuration import (
        BacktestingWeightsConfig,
        FrontEndDetails,
        PortfolioBuildConfiguration,
        PortfolioConfiguration,
        PortfolioMarketsConfig,
    )
    from msm_portfolios.rebalance_strategy import (
        CalendarEventSignal,
        ImmediateSignal,
        ProportionalExecutionCostModel,
    )
    from msm_portfolios.rebalance_strategy.immediate_signal import SESSION_EVENT_TYPES
    from msm_portfolios.time_index import (
        PortfolioCalendarEvents,
        PortfolioCalendarEventsConfiguration,
    )

    from alpaca_connectors.holdings.account import DEFAULT_CASH_ASSET_IDENTIFIER
    from alpaca_connectors.instances import AlpacaPortfolioCalendarEventsStorage
    from alpaca_connectors.market_data.alpaca_bars import AlpacaStockBarsFetch
    from alpaca_connectors.market_data.services import last_session_close_before
    from alpaca_connectors.portfolios.provided_weights import portfolio_signal_key
    from alpaca_connectors.portfolios.signal_definitions import weights_signal

    configuration = resolved.configuration
    rebalance = resolved.rebalance_configuration
    if rebalance.strategy not in ("calendar_event_signal", "immediate_signal"):
        raise ValueError("Portfolio execution requires CalendarEventSignal or ImmediateSignal.")
    signal = weights_signal(portfolio_signal_key(configuration))
    if signal.signal_uid != resolved.signal_uid:
        raise RuntimeError("Resolved Signal identity changed while building the portfolio run.")
    valuation_source = account_price_chain(resolved, price_chains)
    calendar_row = ensure_trading_calendar(rebalance.calendar_identifier)
    if str(calendar_row.unique_identifier) != rebalance.calendar_identifier:
        raise RuntimeError(
            "The stored Portfolio calendar does not match the rebalance configuration."
        )
    # Both strategies read the sessions of the portfolio's calendar (ms-markets ADR 0058): a
    # trade waits for the market to be open, and each asset trades at its next observed price.
    # The events table publishes both session boundaries; `calendar_event_signal`'s own event is
    # one of them. They are this application's private rows. They start at a fixed time, not at
    # the portfolio's signal, so every portfolio of a calendar shares one update (ADR 0033); each
    # rebalance reads only its own window.
    calendar_events = PortfolioCalendarEvents(
        config=PortfolioCalendarEventsConfiguration(
            offset_start=AlpacaStockBarsFetch.OFFSET_START,
            calendar_identifier=rebalance.calendar_identifier,
            session_label=rebalance.session_label,
            event_types=SESSION_EVENT_TYPES,
        ),
        output_table=AlpacaPortfolioCalendarEventsStorage,
    )
    execution_costs = (ProportionalExecutionCostModel(rate=configuration.commission_fee),)
    if rebalance.strategy == "immediate_signal":
        rebalance_strategy = ImmediateSignal(
            calendar_events_instance=calendar_events,
            calendar_identifier=rebalance.calendar_identifier,
            session_label=rebalance.session_label,
            execution_cost_model_instances=execution_costs,
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
            execution_cost_model_instances=execution_costs,
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
            backtesting_weights_configuration=BacktestingWeightsConfig(
                rebalance_strategy_instance=rebalance_strategy,
                signal_weights_instance=signal,
            ),
            accounting_configuration=PortfolioAccountingConfiguration(
                initial_nav=LEDGER_INITIAL_NAV,
                initial_state_time_index=last_session_close_before(resolved.signal_start_time),
                position_valuation_model_instance=MarketPriceValuationModel(
                    maximum_staleness=dt.timedelta(
                        seconds=configuration.valuation_maximum_staleness_seconds
                    ),
                    fail_on_missing_values=configuration.fail_on_missing_prices,
                ),
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


def execute_portfolio_configuration(configuration_uid: Any) -> PortfolioExecutionResult:
    """Run a portfolio configuration with the ms-markets recipe ``run_portfolio`` (ADR 0031).

    ms-markets runs its account's bars and interpolated prices on its profile (ADR 0033), the
    calendar events, the rebalance state into this application's private table, the executed
    weights, then the values, from the signal's first observation and reading only its one
    signal; a failed bars fetch blocks the run. The `Portfolio` row is the configuration's
    ``ALPACA_PORTFOLIO__<hex>``, the ledger is this application's private table, and the weights
    and values, projections of the ledger, are published to Markets. Nothing is
    registered or reconciled. The calls that give the portfolio weights remain the signal
    producer; this run publishes no signal observation.
    """
    from alpaca_connectors.instances import (
        AlpacaPortfolioEventLedgerStorage,
        AlpacaPortfolioRebalanceStateStorage,
    )
    from alpaca_connectors.markets_api import portfolio_weights_recorded, run_portfolio
    from alpaca_connectors.runtime import start_portfolio_job_engine

    # This service is reusable outside the repository Job entrypoint. Bootstrap the complete
    # portfolio model set before configuration readers can initialize a smaller app runtime.
    start_portfolio_job_engine()
    resolved = resolve_portfolio_configuration(configuration_uid)
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
        event_ledger_output_table=AlpacaPortfolioEventLedgerStorage,
    )
    first_rebalance = _first_rebalance(resolved, at=rebalanced_through)
    if first_rebalance["rebalanced"]:
        first_rebalance = _recorded_rebalance(
            first_rebalance, portfolio_weights_recorded(answer.portfolio_unique_identifier)
        )
    return _finish_run(resolved, answer, first_rebalance=first_rebalance)


def _first_rebalance(
    resolved: ResolvedPortfolioConfiguration, *, at: dt.datetime
) -> dict[str, Any]:
    return first_rebalance_state(
        resolved.rebalance_configuration, starts_at=resolved.signal_start_time, at=at
    )


def _recorded_rebalance(first_rebalance: dict[str, Any], recorded: bool) -> dict[str, Any]:
    """The schedule's first rebalance, as Markets holds it (ms-markets issue 118)."""
    return recorded_rebalance_state(first_rebalance, recorded)


def _finish_run(
    resolved: ResolvedPortfolioConfiguration,
    answer: Any,
    *,
    first_rebalance: dict[str, Any],
) -> PortfolioExecutionResult:
    """Record the run's ``Portfolio`` on its configuration and shape its result."""
    portfolio_uid = answer.portfolio_uid
    if (
        resolved.configuration.portfolio_uid is None
        or str(resolved.configuration.portfolio_uid) != portfolio_uid
    ):
        update_portfolio_configuration_row(
            resolved.configuration.uid,
            values={"portfolio_uid": uuid.UUID(portfolio_uid)},
        )
    if not first_rebalance["rebalanced"]:
        # Issue #41: a run before the first configured event writes no weights.
        logger.warning("%s", first_rebalance["message"])
    return PortfolioExecutionResult(
        resolved=resolved,
        portfolio_uid=portfolio_uid,
        portfolio_unique_identifier=answer.portfolio_unique_identifier,
        written=dict(answer.written),
        first_rebalance=first_rebalance,
    )


@dataclass(frozen=True, slots=True)
class PortfolioBatchResult:
    """What one run of several portfolio configurations did: each configuration's result, or
    the error that stopped it, in the order given."""

    results: dict[str, PortfolioExecutionResult | Exception]

    def summary(self) -> dict[str, Any]:
        return {
            "configuration_uids": list(self.results),
            "ran": [
                result.summary()
                for result in self.results.values()
                if isinstance(result, PortfolioExecutionResult)
            ],
            "failed": [
                {"configuration_uid": uid, "error": repr(result)}
                for uid, result in self.results.items()
                if isinstance(result, Exception)
            ],
        }


def resolve_portfolio_configurations(
    configuration_uids: Sequence[Any],
) -> dict[str, ResolvedPortfolioConfiguration | Exception]:
    """``resolve_portfolio_configuration`` for several configurations, one read per kind.

    The configurations, their inputs (``_resolve_inputs``) and the signals' recorded assets are
    each read once for all of them; each signal's latest observation is read together with the
    others. Each configuration is answered with what it resolves to, or with the error that
    blocks it.
    """
    from alpaca_connectors.markets_api import all_together, each_together
    from alpaca_connectors.portfolios.configurations import portfolio_configurations_by_uids
    from alpaca_connectors.portfolios.signal_assets import assets_by_signal

    uids = [str(uuid.UUID(str(value))) for value in configuration_uids]
    rows = portfolio_configurations_by_uids(uids)
    answers: dict[str, ResolvedPortfolioConfiguration | Exception] = {}
    found = [rows[uid] for uid in uids if uid in rows]
    inputs = _resolve_inputs(found) if found else {}
    pending = {uid: value for uid, value in inputs.items() if not isinstance(value, Exception)}
    signals = list(dict.fromkeys(value.signal_uid for value in pending.values()))

    def latest(signal_uid: str) -> Any:
        try:
            return read_signal_observation_matrix(signal_uid=signal_uid, observation_limit=1)
        except Exception as exc:  # noqa: BLE001 - it blocks only the portfolios on this signal
            return exc

    matrices, recorded = (
        all_together(lambda: each_together(latest, signals), lambda: assets_by_signal(signals))
        if signals
        else ([], {})
    )
    latest_by_signal = dict(zip(signals, matrices))
    for uid in uids:
        if uid not in rows:
            answers[uid] = LookupError(f"Portfolio configuration {uid} does not exist.")
            continue
        value = inputs[uid]
        if isinstance(value, Exception):
            answers[uid] = value
            continue
        matrix = latest_by_signal[value.signal_uid]
        if isinstance(matrix, Exception):
            answers[uid] = matrix
            continue
        if not matrix.time_indexes or not matrix.assets:
            answers[uid] = ValueError(
                f"Signal {value.signal_uid} has no published observations. "
                "Publish weights to the portfolio's signal before running it."
            )
            continue
        answers[uid] = ResolvedPortfolioConfiguration(
            configuration=value.configuration,
            bars_configuration=value.bars_configuration,
            rebalance_configuration=value.rebalance_configuration,
            signal_uid=value.signal_uid,
            signal_start_time=value.signal_start_time,
            asset_identifiers=tuple(
                sorted(
                    {asset.asset_identifier for asset in matrix.assets}
                    | set(recorded.get(value.signal_uid, []))
                )
            ),
            source_time_index_meta_table_uid=value.source_time_index_meta_table_uid,
            source_table=value.source_table,
        )
    return answers


def execute_portfolio_configurations(configuration_uids: Sequence[Any]) -> PortfolioBatchResult:
    """Run several portfolio configurations in one MetaTables invocation (``run_portfolios``).

    The run of the dispatcher's tick (ADR 0013, as amended): the configurations resolve
    together (``resolve_portfolio_configurations``); every portfolio of one account and bars
    profile is valued by one price chain (ADR 0033), and every portfolio's ledger is a head of one
    joined graph, so the account's bars, its prices and a calendar's events run once and no
    portfolio waits for another process; each publishes its own weights and values. A
    configuration that does not resolve, build or run is answered with its error; it never stops
    the others, but a failed bars fetch blocks every portfolio its prices value.
    """
    from alpaca_connectors.instances import (
        AlpacaPortfolioEventLedgerStorage,
        AlpacaPortfolioRebalanceStateStorage,
    )
    from alpaca_connectors.markets_api import (
        PortfolioRunEntry,
        each_together,
        portfolio_weights_recorded,
        run_portfolios,
    )
    from alpaca_connectors.runtime import start_portfolio_job_engine

    start_portfolio_job_engine()
    results: dict[str, Any] = dict(resolve_portfolio_configurations(configuration_uids))
    resolved = {
        uid: value
        for uid, value in results.items()
        if isinstance(value, ResolvedPortfolioConfiguration)
    }

    plans: dict[str, PortfolioRunPlan] = {}
    price_chains: dict[tuple[Any, ...], Any] = {}
    for uid, value in resolved.items():
        try:
            plans[uid] = build_portfolio_run(value, price_chains=price_chains)
        except Exception as exc:  # noqa: BLE001 - one configuration's failure stops only it
            results[uid] = exc
    # The rebalances read the events up to their own start, which is after this time.
    rebalanced_through = dt.datetime.now(dt.UTC)
    order = list(plans)
    answers = (
        run_portfolios(
            [
                PortfolioRunEntry(
                    portfolio_configuration=plans[uid].portfolio_configuration,
                    unique_identifier=plans[uid].unique_identifier,
                    calendar_uid=plans[uid].calendar_uid,
                )
                for uid in order
            ],
            rebalance_output_table=AlpacaPortfolioRebalanceStateStorage,
            event_ledger_output_table=AlpacaPortfolioEventLedgerStorage,
        )
        if order
        else []
    )
    ran = dict(zip(order, answers))
    firsts = {
        uid: _first_rebalance(resolved[uid], at=rebalanced_through)
        for uid, answer in ran.items()
        if not isinstance(answer, BaseException)
    }
    # Markets is asked only about the portfolios whose first rebalance was due, together.
    due = [uid for uid, first in firsts.items() if first["rebalanced"]]
    recorded = dict(
        zip(
            due,
            each_together(
                lambda uid: portfolio_weights_recorded(ran[uid].portfolio_unique_identifier), due
            ),
        )
    )
    for uid, answer in ran.items():
        if isinstance(answer, BaseException):
            results[uid] = answer if isinstance(answer, Exception) else RuntimeError(str(answer))
            continue
        first = firsts[uid]
        if uid in recorded:
            first = _recorded_rebalance(first, recorded[uid])
        try:
            results[uid] = _finish_run(resolved[uid], answer, first_rebalance=first)
        except Exception as exc:  # noqa: BLE001 - one configuration's failure stops only it
            results[uid] = exc
    return PortfolioBatchResult(results=results)


__all__ = [
    "HeldPortfolioInputs",
    "PortfolioExecutionResult",
    "PortfolioRunPlan",
    "PortfolioPreflight",
    "ResolvedPortfolioConfiguration",
    "account_price_chain",
    "build_portfolio_run",
    "PortfolioBatchResult",
    "execute_portfolio_configuration",
    "execute_portfolio_configurations",
    "portfolio_unique_identifier",
    "preflight_portfolio_configuration",
    "preflight_portfolio_configurations",
    "resolve_portfolio_configuration",
    "resolve_portfolio_configurations",
]
