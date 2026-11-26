from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from msm_portfolios.recipes import PortfolioRun
from msm_portfolios.services.series import SeriesPublication

from alpaca_connectors.portfolios import configurations, execution
from alpaca_connectors.portfolios.configurations import (
    AlpacaPortfolioConfiguration,
    AlpacaPortfolioConfigurationTable,
    PortfolioRebalanceConfiguration,
    normalize_rebalance_strategy,
    update_portfolio_configuration,
)
from alpaca_connectors.portfolios.execution import (
    PortfolioPreflight,
    PortfolioRunPlan,
    ResolvedPortfolioConfiguration,
    execute_portfolio_configuration,
    preflight_portfolio_configuration,
    resolve_portfolio_configuration,
)
from alpaca_connectors.portfolios.signal_observations import (
    SignalObservationAsset,
    SignalObservationBounds,
    SignalObservationMatrix,
)

CONFIGURATION_UID = uuid.UUID("11111111-1111-4111-8111-111111111111")
BARS_CONFIGURATION_UID = uuid.UUID("33333333-3333-4333-8333-333333333333")
REBALANCE_CONFIGURATION_UID = uuid.UUID("44444444-4444-4444-8444-444444444444")
PORTFOLIO_UID = uuid.UUID("55555555-5555-4555-8555-555555555555")
ACCOUNT_UID = uuid.UUID("66666666-6666-4666-8666-666666666666")
DEFINITION_UID = uuid.UUID("77777777-7777-4777-8777-777777777777")


def portfolio_configuration(**overrides) -> AlpacaPortfolioConfiguration:
    now = dt.datetime(2026, 9, 5, 9, tzinfo=dt.UTC)
    values = {
        "uid": CONFIGURATION_UID,
        "name": "Daily analytical portfolio",
        "description": "NYSE-close portfolio backtest.",
        "owner_gateway_uid": "owner-uid",
        "owner_user_uid": "owner-uid",
        "account_uid": ACCOUNT_UID,
        "signal_definition_uid": DEFINITION_UID,
        "bars_configuration_uid": None,
        "rebalance_configuration_uid": REBALANCE_CONFIGURATION_UID,
        "portfolio_uid": None,
        "scheduled": True,
        "upsample_frequency_id": "1d",
        "intraday_bar_interpolation_rule": "ffill",
        "valuation_column": "close",
        "valuation_maximum_staleness_seconds": 86_400,
        "fail_on_missing_prices": True,
        "commission_fee": 0.00018,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return AlpacaPortfolioConfiguration.model_validate(values)


def rebalance_configuration() -> PortfolioRebalanceConfiguration:
    now = dt.datetime(2026, 9, 5, 9, tzinfo=dt.UTC)
    return PortfolioRebalanceConfiguration.model_validate(
        {
            "uid": REBALANCE_CONFIGURATION_UID,
            "name": "NYSE close",
            "description": "Use the latest observed signal at each NYSE close.",
            "strategy": "calendar_event_signal",
            "calendar_identifier": "NYSE",
            "session_label": "regular",
            "rebalance_event": "market_close",
            "event_offset_seconds": 0,
            "rebalance_cadence": "every_session",
            "rebalance_weekday": 0,
            "created_at": now,
            "updated_at": now,
        }
    )


def resolved_configuration() -> ResolvedPortfolioConfiguration:
    return ResolvedPortfolioConfiguration(
        configuration=portfolio_configuration(),
        bars_configuration=SimpleNamespace(
            uid=BARS_CONFIGURATION_UID,
            frequency_id="1d",
            feed="sip",
            adjustment="all",
        ),
        rebalance_configuration=rebalance_configuration(),
        signal_uid="signal-uid",
        signal_start_time=dt.datetime(2026, 9, 4, 14, tzinfo=dt.UTC),
        asset_identifiers=("ALPACA::asset-a", "ALPACA::asset-b"),
        source_time_index_meta_table_uid="source-table-uid",
    )


def test_portfolio_configuration_owns_calculation_fields_and_the_scheduled_flag() -> None:
    columns = AlpacaPortfolioConfigurationTable.__table__.c

    for forbidden in (
        "environment_uid",
        "schedule_type",
        "schedule_every",
        "schedule_expression",
        "schedule_timezone",
        "cpu_request",
        "memory_request",
        "spot",
        "max_runtime_seconds",
        "image_status",
        "latest_run_status",
        "universe_uid",
        "source_time_index_meta_table_uid",
        # One weights source (ADR 0027): no signal configuration, no unused job_uid.
        "weights_source",
        "signal_configuration_uid",
        "job_uid",
    ):
        assert forbidden not in columns
    # The portfolio belongs to its account, which cannot be deleted while it has portfolios, and
    # follows one of its account's signal definitions (ADR 0028).
    [account_key] = [
        key
        for key in columns.account_uid.foreign_keys
        if key.column.table.name == "ms_markets__account"
    ]
    assert account_key.ondelete == "RESTRICT"
    [definition_key] = [
        key
        for key in AlpacaPortfolioConfigurationTable.__table__.foreign_key_constraints
        if "signal_definition_uid" in key.column_keys
    ]
    assert definition_key.column_keys == ["signal_definition_uid", "account_uid"]
    assert [element.target_fullname for element in definition_key.elements] == [
        "alpaca_connectors__signal_definition.uid",
        "alpaca_connectors__signal_definition.account_uid",
    ]
    # Its bars configuration names it instead; the former key is gone.
    assert not columns.bars_configuration_uid.foreign_keys
    assert columns.rebalance_configuration_uid.foreign_keys
    assert columns.portfolio_uid.foreign_keys
    assert columns.scheduled.nullable is False
    assert columns.scheduled.server_default is not None
    # Every portfolio has its owner (ADR 0024, decision 6).
    assert columns.owner_gateway_uid.nullable is False
    assert columns.owner_user_uid.nullable is False


def test_rebalance_accepts_only_calendar_event_signal() -> None:
    assert normalize_rebalance_strategy("CALENDAR_EVENT_SIGNAL") == "calendar_event_signal"
    with pytest.raises(ValueError, match="calendar_event_signal"):
        normalize_rebalance_strategy("time_weighted")


def test_pausing_a_portfolio_writes_only_the_scheduled_flag() -> None:
    with (
        patch.object(
            configurations, "get_portfolio_configuration", return_value=portfolio_configuration()
        ),
        patch.object(configurations, "validate_portfolio_references") as validate,
        patch.object(configurations, "update_portfolio_configuration_row") as update_row,
    ):
        update_portfolio_configuration(CONFIGURATION_UID, scheduled=False)

    # A disabled bars configuration must not stop a portfolio from being paused.
    validate.assert_not_called()
    update_row.assert_called_once_with(CONFIGURATION_UID, values={"scheduled": False})


def test_calculation_updates_still_validate_their_references() -> None:
    with (
        patch.object(
            configurations, "get_portfolio_configuration", return_value=portfolio_configuration()
        ),
        patch.object(
            configurations,
            "validate_portfolio_references",
            side_effect=ValueError("Bars configuration x is disabled."),
        ) as validate,
        patch.object(configurations, "update_portfolio_configuration_row") as update_row,
        pytest.raises(ValueError, match="is disabled"),
    ):
        update_portfolio_configuration(CONFIGURATION_UID, commission_fee=0.0, scheduled=True)

    validate.assert_called_once()
    update_row.assert_not_called()


def test_preflight_reports_resolution_blockers_without_writing() -> None:
    row = portfolio_configuration()
    with (
        patch.object(execution, "get_portfolio_configuration", return_value=row) as read,
        patch.object(
            execution,
            "_resolve_inputs",
            return_value={
                str(CONFIGURATION_UID): ValueError("Signal x has no published observations.")
            },
        ) as resolve,
    ):
        blocked = preflight_portfolio_configuration(CONFIGURATION_UID)
    with (
        patch.object(execution, "get_portfolio_configuration", return_value=row),
        patch.object(
            execution, "_resolve_inputs", return_value={str(CONFIGURATION_UID): object()}
        ) as resolve_allowed,
    ):
        allowed = preflight_portfolio_configuration(CONFIGURATION_UID)

    # The configuration is read once, and its inputs resolved from that row.
    read.assert_called_once_with(CONFIGURATION_UID)
    resolve.assert_called_once_with([row], held=None)
    resolve_allowed.assert_called_once_with([row], held=None)
    assert blocked == PortfolioPreflight(
        configuration_uid=str(CONFIGURATION_UID),
        blockers=("Signal x has no published observations.",),
    )
    assert not blocked.allowed
    assert allowed.allowed


def _input_reads(rows, *, bounds, rebalance=None, own_bars=None):
    """Replace each batch read of a preflight; answer the recorder of their calls."""
    reads = Mock()
    reads.rebalances.return_value = {
        str(REBALANCE_CONFIGURATION_UID): rebalance or rebalance_configuration()
    }
    reads.own_bars.return_value = (
        own_bars
        if own_bars is not None
        else {
            str(row.uid): SimpleNamespace(
                uid=uuid.uuid4(), enabled=True, frequency_id="1d", feed="sip", adjustment="all"
            )
            for row in rows
        }
    )
    reads.named_bars.return_value = {}
    reads.definitions.return_value = {
        str(row.signal_definition_uid): SimpleNamespace(signal_key=f"key-{row.uid}") for row in rows
    }
    reads.bounds.side_effect = lambda signals: {signal: bounds for signal in signals}
    reads.sources.return_value = {("1d", "sip", "all"): SimpleNamespace(uid="source-table-uid")}
    patches = (
        patch(f"{CONFIGURATIONS}.rebalance_configurations_by_uids", reads.rebalances),
        patch(f"{BARS}.portfolio_bar_configurations", reads.own_bars),
        patch(f"{BARS}.bar_configuration_rows_by_uids", reads.named_bars),
        patch(f"{DEFINITIONS}.signal_definitions_by_uids", reads.definitions),
        patch(f"{HISTORY}.read_signal_observation_bounds_by_signal", reads.bounds),
        patch.object(execution, "resolve_alpaca_bars_tables", reads.sources),
        # The reads share one engine, started first; no backend here.
        patch("alpaca_connectors.runtime.start_markets_engine"),
    )
    return reads, patches


CONFIGURATIONS = "alpaca_connectors.portfolios.configurations"
BARS = "alpaca_connectors.market_data.configurations"
DEFINITIONS = "alpaca_connectors.portfolios.signal_definitions"
HISTORY = "alpaca_connectors.portfolios.signal_observations"
OBSERVED = SignalObservationBounds(
    signal_uid="any",
    first_observation_at=dt.datetime(2026, 9, 3, 8, tzinfo=dt.UTC),
    last_observation_at=dt.datetime(2026, 9, 5, 8, tzinfo=dt.UTC),
)


def test_many_preflights_read_each_kind_of_input_once() -> None:
    rows = [
        portfolio_configuration(
            uid=uuid.UUID(int=index), signal_definition_uid=uuid.UUID(int=100 + index)
        )
        for index in range(1, 4)
    ]
    reads, patches = _input_reads(rows, bounds=OBSERVED)
    # The third has a disabled feed: blocked, the others are not.
    reads.own_bars.return_value[str(rows[2].uid)] = SimpleNamespace(
        uid=BARS_CONFIGURATION_UID, enabled=False, frequency_id="1d", feed="sip", adjustment="all"
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        answers = execution.preflight_portfolio_configurations(rows)

    # One read per kind for all of them (issue #46), never one per configuration.
    for read in (reads.rebalances, reads.own_bars, reads.definitions, reads.bounds, reads.sources):
        read.assert_called_once()
    reads.rebalances.assert_called_once_with([str(REBALANCE_CONFIGURATION_UID)] * 3)
    assert len(reads.bounds.call_args.args[0]) == 2
    assert [answers[str(row.uid)].allowed for row in rows] == [True, True, False]
    assert answers[str(rows[2].uid)].blockers == (
        f"Bars configuration {BARS_CONFIGURATION_UID} is disabled.",
    )


def test_a_signal_without_observations_blocks_its_portfolio() -> None:
    row = portfolio_configuration()
    empty = SignalObservationBounds(
        signal_uid="any", first_observation_at=None, last_observation_at=None
    )
    reads, patches = _input_reads([row], bounds=empty)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        answer = execution.preflight_portfolio_configurations([row])[str(row.uid)]
    assert not answer.allowed
    assert "has no published observations" in answer.blockers[0]


def test_held_inputs_are_not_read_again() -> None:
    # A create starts its first run with what it just wrote (issue #46).
    row = portfolio_configuration()
    bars = SimpleNamespace(
        uid=BARS_CONFIGURATION_UID, enabled=True, frequency_id="1d", feed="sip", adjustment="all"
    )
    definition = SimpleNamespace(signal_key="key")
    from alpaca_connectors.portfolios.signal_definitions import signal_uid_for_key

    held = execution.HeldPortfolioInputs(
        rebalance_configurations={str(REBALANCE_CONFIGURATION_UID): rebalance_configuration()},
        bars_configurations={str(row.uid): bars},
        definitions={str(DEFINITION_UID): definition},
        signal_bounds={signal_uid_for_key("key"): OBSERVED},
    )
    reads, patches = _input_reads([row], bounds=OBSERVED)
    # Asked for nothing, each read answers nothing.
    for read in (reads.rebalances, reads.own_bars, reads.definitions):
        read.return_value = {}
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        answer = execution.preflight_portfolio_configurations([row], held=held)[str(row.uid)]

    assert answer.allowed
    for read in (
        reads.rebalances,
        reads.own_bars,
        reads.named_bars,
        reads.definitions,
        reads.bounds,
    ):
        assert read.call_args.args[0] in ([], ()) or not list(read.call_args.args[0])
    reads.sources.assert_called_once()


def test_a_configuration_row_update_is_one_statement() -> None:
    # No read first (issue #46): the update's RETURNING row answers, and none means none exists.
    row = portfolio_configuration()
    with (
        patch.object(configurations, "_start_runtime"),
        patch.object(AlpacaPortfolioConfiguration, "get_by_uid") as read,
        patch.object(AlpacaPortfolioConfiguration, "update", return_value=row) as update,
    ):
        assert (
            configurations.update_portfolio_configuration_row(
                CONFIGURATION_UID, values={"scheduled": False}
            )
            is row
        )
        update.side_effect = LookupError("no row")
        with pytest.raises(LookupError, match=f"Portfolio configuration {CONFIGURATION_UID}"):
            configurations.update_portfolio_configuration_row(
                CONFIGURATION_UID, values={"scheduled": False}
            )
    read.assert_not_called()
    assert update.call_args.args[1]["scheduled"] is False


def test_preflight_of_a_missing_configuration_is_not_found() -> None:
    with (
        patch.object(execution, "get_portfolio_configuration", return_value=None),
        pytest.raises(LookupError, match="does not exist"),
    ):
        preflight_portfolio_configuration(CONFIGURATION_UID)


def test_portfolio_resolution_uses_published_signal_and_selected_bars_profile() -> None:
    row = portfolio_configuration()
    matrix = SignalObservationMatrix(
        signal_uid="signal-uid",
        time_indexes=(dt.datetime(2026, 9, 5, 8, tzinfo=dt.UTC),),
        assets=(
            SignalObservationAsset("ALPACA::asset-b", "B", None, (0.4,)),
            SignalObservationAsset("ALPACA::asset-a", "A", None, (0.6,)),
        ),
    )
    reads, patches = _input_reads([row], bounds=OBSERVED)

    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patch(
            "alpaca_connectors.portfolios.execution.get_portfolio_configuration", return_value=row
        ),
        patch(
            "alpaca_connectors.portfolios.signal_assets.assets_for_signals",
            return_value=["ALPACA::asset-a"],
        ),
        patch(
            "alpaca_connectors.portfolios.execution.read_signal_observation_matrix",
            return_value=matrix,
        ) as read_signal,
    ):
        resolved = resolve_portfolio_configuration(CONFIGURATION_UID)

    from alpaca_connectors.portfolios.signal_definitions import signal_uid_for_key

    signal_uid = signal_uid_for_key(f"key-{row.uid}")
    assert resolved.signal_uid == signal_uid
    assert resolved.asset_identifiers == ("ALPACA::asset-a", "ALPACA::asset-b")
    assert resolved.signal_start_time == OBSERVED.first_observation_at
    assert resolved.source_time_index_meta_table_uid == "source-table-uid"
    read_signal.assert_called_once_with(signal_uid=signal_uid, observation_limit=1)
    reads.bounds.assert_called_once_with([signal_uid])
    reads.sources.assert_called_once_with([("1d", "sip", "all")])


@pytest.mark.parametrize("recorded", [True, False])
def test_execution_runs_the_portfolio_with_the_recipe_and_registers_nothing(
    recipes, recorded
) -> None:
    from alpaca_connectors.instances import AlpacaPortfolioRebalanceStateStorage

    resolved = resolved_configuration()
    plan = PortfolioRunPlan(
        calendar_events=Mock(),
        portfolio_configuration=Mock(),
        portfolio_description="NYSE-close portfolio backtest.",
        unique_identifier="ALPACA_PORTFOLIO__11111111111141118111111111111111",
        calendar_uid="calendar-uid",
    )
    order = Mock()
    portfolio_row = SimpleNamespace(uid=PORTFOLIO_UID, unique_identifier=plan.unique_identifier)
    answer = PortfolioRun(
        portfolio=portfolio_row,
        weights=SeriesPublication(key=plan.unique_identifier, written=2, skipped=0),
        values=SeriesPublication(key=plan.unique_identifier, written=3, skipped=0),
        first_rebalance=True,
    )
    resolved_rows = []

    def run(portfolio_configuration, *, portfolio_resolver=None, **kwargs):
        order.run()
        # The run asks this application for its Portfolio row.
        resolved_rows.append(
            portfolio_resolver.get_or_create_from_configuration_hash(
                portfolio_configuration_hash="hash",
                portfolio_configuration=portfolio_configuration,
                timeout=None,
            )
        )
        return answer

    recipes.answer("run_portfolio", run)

    with (
        patch("alpaca_connectors.runtime.start_portfolio_job_engine") as start_engine,
        patch(
            "alpaca_connectors.portfolios.execution.resolve_portfolio_configuration",
            return_value=resolved,
        ),
        patch(
            "alpaca_connectors.portfolios.execution.build_portfolio_run",
            side_effect=lambda resolved: (order.plan(), plan)[1],
        ),
        patch(
            "alpaca_connectors.portfolios.execution.update_portfolio_configuration_row"
        ) as update_row,
        patch(
            "alpaca_connectors.portfolios.execution.first_rebalance_state",
            return_value={"rebalanced": True, "first_rebalance_at": "2026-11-18T21:00:00+00:00"},
        ) as first_rebalance,
        patch("msm.api.portfolios.Portfolio.upsert", return_value=portfolio_row) as upsert,
        patch(
            "alpaca_connectors.markets_api.portfolio_weights_recorded", return_value=recorded
        ) as weights_recorded,
    ):
        result = execute_portfolio_configuration(CONFIGURATION_UID)

    # The summary says whether the run reached the first rebalance (issue #41).
    assert first_rebalance.call_args.args == (resolved.rebalance_configuration,)
    assert first_rebalance.call_args.kwargs["starts_at"] == resolved.signal_start_time
    # A due rebalance counts only when Markets holds the portfolio's weights (ms-markets #118).
    weights_recorded.assert_called_once_with(plan.unique_identifier)
    if recorded:
        assert result.first_rebalance == first_rebalance.return_value
    else:
        assert result.first_rebalance == {
            "rebalanced": False,
            "first_rebalance_at": "2026-11-18T21:00:00+00:00",
            "message": (
                "The portfolio's first rebalance was due at 2026-11-18T21:00:00+00:00, and "
                "Markets holds no weights of it: no run has recorded that rebalance."
            ),
        }
    # The bars are a node of the run's graph (ADR 0033): the plan, then the run, once.
    start_engine.assert_called_once_with()
    assert [name for name, *_ in order.mock_calls] == ["plan", "run"]
    # The configuration this application built (its prices, calendar events and signal), its
    # private rebalance-state table; ms-markets starts it at the signal's first observation.
    [(name, arguments)] = recipes.calls
    assert name == "run_portfolio"
    assert arguments["portfolio_configuration"] is plan.portfolio_configuration
    assert arguments["portfolio_description"] == plan.portfolio_description
    assert arguments["rebalance_output_table"] is AlpacaPortfolioRebalanceStateStorage
    # The Portfolio is the configuration's ALPACA_PORTFOLIO__<hex>, on its calendar.
    upsert.assert_called_once_with(
        unique_identifier=plan.unique_identifier, calendar_uid="calendar-uid"
    )
    assert resolved_rows == [portfolio_row]
    # The configuration stores the Portfolio it runs; nothing is registered or reconciled.
    update_row.assert_called_once_with(
        CONFIGURATION_UID,
        values={"portfolio_uid": PORTFOLIO_UID},
    )
    assert result.portfolio_uid == str(PORTFOLIO_UID)
    assert result.portfolio_unique_identifier == plan.unique_identifier
    summary = result.summary()
    # The recipe counts only the published rows.
    assert summary["portfolio_weights"] == {"error": False, "rows": 2}
    assert summary["portfolio"] == {"error": False, "rows": 3}
    assert (summary["interpolation"], summary["calendar_events"], summary["rebalance"]) == (
        None,
        None,
        None,
    )


def test_a_run_whose_portfolio_is_stored_does_not_store_it_again(recipes) -> None:
    resolved = resolved_configuration()
    resolved = ResolvedPortfolioConfiguration(
        **{
            **{name: getattr(resolved, name) for name in resolved.__slots__},
            "configuration": portfolio_configuration(portfolio_uid=PORTFOLIO_UID),
        }
    )
    recipes.answer(
        "run_portfolio",
        PortfolioRun(
            portfolio=SimpleNamespace(uid=PORTFOLIO_UID, unique_identifier=None),
            weights=None,
            values=None,
            first_rebalance=False,
        ),
    )
    with (
        patch("alpaca_connectors.runtime.start_portfolio_job_engine"),
        patch.object(execution, "resolve_portfolio_configuration", return_value=resolved),
        patch.object(
            execution,
            "build_portfolio_run",
            return_value=PortfolioRunPlan(
                calendar_events=None,
                portfolio_configuration=Mock(),
                portfolio_description="d",
                unique_identifier=execution.portfolio_unique_identifier(CONFIGURATION_UID),
                calendar_uid="calendar-uid",
            ),
        ),
        patch.object(execution, "update_portfolio_configuration_row") as update_row,
        patch.object(
            execution,
            "first_rebalance_state",
            return_value={"rebalanced": False, "message": "No rebalance yet."},
        ),
    ):
        result = execute_portfolio_configuration(CONFIGURATION_UID)

    update_row.assert_not_called()
    # Its identifier is the configuration's when Markets does not repeat it.
    assert result.portfolio_unique_identifier == execution.portfolio_unique_identifier(
        CONFIGURATION_UID
    )
    assert result.first_rebalance == {"rebalanced": False, "message": "No rebalance yet."}


def _real_run_plan(strategy: str):
    """``build_portfolio_run`` with the real ms-markets objects, offline.

    Only what needs the MetaTables backend is replaced: the registration of the calendar-events
    table, the account's price chain (a real ``TimeIndexTableRef`` of an unregistered table) and
    the stored calendar. Every configuration and strategy object is ms-markets' own, so a field it requires,
    refuses or no longer has fails here.
    """
    import metatables.updaters.updaters as updaters
    from alpaca_connectors.portfolios.signal_definitions import signal_uid_for_key
    from metatables import TimeIndexMetaTable, TimeIndexTableRef

    resolved = resolved_configuration()
    resolved = ResolvedPortfolioConfiguration(
        **{
            **{name: getattr(resolved, name) for name in resolved.__slots__},
            "rebalance_configuration": rebalance_configuration().model_copy(
                update={"strategy": strategy}
            ),
            "signal_uid": signal_uid_for_key("signal-key"),
        }
    )
    prices = TimeIndexTableRef(
        output_table=TimeIndexMetaTable.model_construct(
            uid=str(uuid.UUID(int=21)),
            data_source_uid=str(uuid.UUID(int=22)),
            identifier="alpaca_connectors__interpolated_prices__t_x",
            physical_table_name="alpaca_connectors__interpolated_prices__t_x",
        )
    )
    with (
        patch.object(
            updaters,
            "ensure_registered_output_table",
            lambda output_table, context: output_table,
        ),
        patch.object(execution, "build_account_interpolated_prices", return_value=prices),
        patch.object(
            execution,
            "ensure_trading_calendar",
            return_value=SimpleNamespace(uid="calendar-uid", unique_identifier="NYSE"),
        ),
        patch(
            "alpaca_connectors.portfolios.provided_weights.portfolio_signal_key",
            return_value="signal-key",
        ),
        patch("msm.api.portfolios.Portfolio.upsert") as upsert,
    ):
        plan = execution.build_portfolio_run(resolved)
    # The run creates or updates the Portfolio, not the build (ADR 0031).
    upsert.assert_not_called()
    return plan


@pytest.mark.parametrize("strategy", ["immediate_signal", "calendar_event_signal"])
def test_both_strategies_read_the_calendar_sessions_and_build_with_ms_markets(strategy) -> None:
    """ms-markets ADR 0058: every rebalance waits for an open market and trades at the next
    observed price, so both strategies read the portfolio calendar's open and close."""
    from msm_portfolios.accounting import MarketPriceValuationModel
    from msm_portfolios.configuration import PortfolioConfiguration
    from msm_portfolios.contrib.signals import ApplicationWeights
    from msm_portfolios.rebalance_strategy import (
        CalendarEventSignal,
        ImmediateSignal,
        ProportionalExecutionCostModel,
    )
    from msm_portfolios.rebalance_strategy.base import require_observed_price_column
    from msm_portfolios.time_index import PortfolioCalendarEvents

    from alpaca_connectors.instances import AlpacaPortfolioCalendarEventsStorage
    from alpaca_connectors.market_data.alpaca_bars import AlpacaStockBarsFetch

    plan = _real_run_plan(strategy)

    assert isinstance(plan.portfolio_configuration, PortfolioConfiguration)
    build = plan.portfolio_configuration.portfolio_build_configuration
    weights = build.backtesting_weights_configuration
    rule = weights.rebalance_strategy_instance
    expected = ImmediateSignal if strategy == "immediate_signal" else CalendarEventSignal
    assert type(rule) is expected
    assert isinstance(weights.signal_weights_instance, ApplicationWeights)
    # The private calendar-events table, publishing both session boundaries of the calendar.
    events = plan.calendar_events
    assert isinstance(events, PortfolioCalendarEvents)
    assert rule.calendar_events_instance is events
    assert events.output_table is AlpacaPortfolioCalendarEventsStorage
    assert tuple(events.config.event_types) == ("market_open", "market_close")
    assert (events.config.calendar_identifier, events.config.session_label) == ("NYSE", "regular")
    # Every portfolio of a calendar shares its events: they start at a fixed time (ADR 0033).
    assert events.config.offset_start == AlpacaStockBarsFetch.OFFSET_START
    assert (rule.calendar_identifier, rule.session_label) == ("NYSE", "regular")
    # Trades at the next observed price, with ms-markets' execution policy defaults.
    assert rule.fill_rule == "next_observed_price"
    assert rule.execution_policy == type(rule.execution_policy)()
    # The portfolio's currency is the Alpaca account's cash (ms-markets ADR 0059).
    assert build.valuation_asset_identifier == "USD"
    assert build.fx_source_instance is None
    require_observed_price_column(build.valuation_column)
    # Every portfolio keeps a ledger (ms-markets ADR 0065): it opens in cash at the signal's first
    # observation and values each position with the configuration's staleness and missing-price
    # rule; the commission is a proportional cost of each fill.
    stored = resolved_configuration().configuration
    accounting = build.accounting_configuration
    assert accounting.initial_nav == execution.LEDGER_INITIAL_NAV
    assert accounting.initial_state_time_index == resolved_configuration().signal_start_time
    valuation = accounting.position_valuation_model_instance
    assert type(valuation) is MarketPriceValuationModel
    assert valuation.maximum_staleness == dt.timedelta(
        seconds=stored.valuation_maximum_staleness_seconds
    )
    assert valuation.fail_on_missing_values is stored.fail_on_missing_prices
    [cost] = rule.execution_cost_model_instances
    assert type(cost) is ProportionalExecutionCostModel
    assert cost.rate == stored.commission_fee
    if strategy == "calendar_event_signal":
        assert (rule.rebalance_event, rule.rebalance_cadence) == ("market_close", "every_session")
    front_end = plan.portfolio_configuration.portfolio_markets_configuration.front_end_details
    assert front_end.rebalance_strategy_name == expected.__name__
    assert "next observed price" in front_end.rebalance_strategy_description


def test_ms_markets_refuses_a_calendar_rule_without_both_session_events() -> None:
    """Why the events table publishes both: one event type is refused (ms-markets ADR 0058)."""
    from msm_portfolios.rebalance_strategy import CalendarEventSignal

    plan = _real_run_plan("calendar_event_signal")
    events = plan.calendar_events
    events.config = events.config.model_copy(update={"event_types": ("market_close",)})
    with pytest.raises(ValueError, match="market_open"):
        CalendarEventSignal(
            calendar_events_instance=events,
            calendar_identifier="NYSE",
            rebalance_event="market_close",
        )


def test_both_rebalance_strategies_are_accepted() -> None:
    assert normalize_rebalance_strategy("immediate_signal") == "immediate_signal"
    assert normalize_rebalance_strategy("calendar_event_signal") == "calendar_event_signal"
    with pytest.raises(ValueError, match="calendar_event_signal or immediate_signal"):
        normalize_rebalance_strategy("time_weighted")


def test_the_list_filters_on_the_key_a_create_was_given() -> None:
    """GET /v1/portfolio-configurations?key=… : one statement, through the signal definitions."""
    from sqlalchemy.dialects import postgresql

    from alpaca_connectors.portfolios.configurations import list_portfolio_configurations

    statements = []

    def compile_statement(statement, **_):
        statements.append(statement)
        return statement

    with (
        patch.object(configurations, "_start_runtime"),
        patch("msm.bootstrap.resolve_runtime", return_value=SimpleNamespace(context=None)),
        patch("msm.repositories.base.compile_markets_statement", side_effect=compile_statement),
        patch(
            "msm.repositories.base.execute_markets_operation",
            side_effect=[{"rows": []}, {"rows": [{"count": 0}]}],
        ),
    ):
        rows, total = list_portfolio_configurations(key="hunch-17")

    assert (rows, total) == ([], 0)
    page = statements[0].compile(dialect=postgresql.dialect())
    sql = str(page)
    assert "signal_definition_uid IN (SELECT" in sql
    assert "alpaca_connectors__signal_definition.key = " in sql
    assert "hunch-17" in page.params.values()
