from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from alpaca_connectors.portfolios import configurations, execution
from alpaca_connectors.portfolios.configurations import (
    AlpacaPortfolioConfiguration,
    AlpacaPortfolioConfigurationTable,
    PortfolioRebalanceConfiguration,
    normalize_rebalance_strategy,
    update_portfolio_configuration,
)
from alpaca_connectors.portfolios.execution import (
    PortfolioExecutionGraph,
    PortfolioPreflight,
    ResolvedPortfolioConfiguration,
    execute_portfolio_configuration,
    preflight_portfolio_configuration,
    resolve_portfolio_configuration,
)
from alpaca_connectors.portfolios.signal_history import (
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
    reads.sources.return_value = {("1d", "sip", "all"): "source-table-uid"}
    patches = (
        patch(f"{CONFIGURATIONS}.rebalance_configurations_by_uids", reads.rebalances),
        patch(f"{BARS}.portfolio_bar_configurations", reads.own_bars),
        patch(f"{BARS}.bar_configuration_rows_by_uids", reads.named_bars),
        patch(f"{DEFINITIONS}.signal_definitions_by_uids", reads.definitions),
        patch(f"{HISTORY}.read_signal_observation_bounds_by_signal", reads.bounds),
        patch.object(execution, "resolve_alpaca_bars_time_index_meta_table_uids", reads.sources),
    )
    return reads, patches


CONFIGURATIONS = "alpaca_connectors.portfolios.configurations"
BARS = "alpaca_connectors.market_data.configurations"
DEFINITIONS = "alpaca_connectors.portfolios.signal_definitions"
HISTORY = "alpaca_connectors.portfolios.signal_history"
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
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
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
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
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
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
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


def test_execution_updates_each_released_stage_without_tree_walk() -> None:
    resolved = resolved_configuration()
    valuation_source = Mock()
    calendar_events = Mock()
    portfolio_rebalance = Mock()
    portfolio_weights = Mock()
    portfolio_node = Mock()
    signal_statistics = Mock()
    portfolio_node.signal_weights.get_update_statistics.return_value = signal_statistics
    portfolio_node.target_portfolio = SimpleNamespace(
        uid=PORTFOLIO_UID,
        unique_identifier="ALPACA_PORTFOLIO__11111111111141118111111111111111",
    )
    portfolio_row = portfolio_node.target_portfolio
    manager = Mock()
    manager.attach_mock(valuation_source.run, "interpolation")
    manager.attach_mock(calendar_events.run, "calendar_events")
    manager.attach_mock(portfolio_rebalance.run, "rebalance")
    manager.attach_mock(portfolio_weights.run, "portfolio_weights")
    manager.attach_mock(portfolio_node.run, "portfolio")

    with (
        patch("alpaca_connectors.runtime.start_portfolio_job_engine") as start_engine,
        patch(
            "alpaca_connectors.portfolios.execution.resolve_portfolio_configuration",
            return_value=resolved,
        ),
        patch(
            "alpaca_connectors.portfolios.execution.build_portfolio_graph",
            return_value=PortfolioExecutionGraph(
                valuation_source=valuation_source,
                calendar_events=calendar_events,
                portfolio_rebalance=portfolio_rebalance,
                portfolio_weights=portfolio_weights,
                portfolio_node=portfolio_node,
                portfolio_row=portfolio_row,
            ),
        ),
        patch(
            "alpaca_connectors.portfolios.execution.update_portfolio_configuration_row"
        ) as update_row,
        patch(
            "alpaca_connectors.portfolios.datasets.register_portfolio_datasets"
        ) as register_datasets,
        patch(
            "alpaca_connectors.portfolios.execution.update_portfolio_bars",
            return_value={"updated": True},
        ) as bars_update,
        patch(
            "alpaca_connectors.portfolios.execution.first_rebalance_state",
            return_value={"rebalanced": True, "first_rebalance_at": "2026-11-18T21:00:00+00:00"},
        ) as first_rebalance,
    ):
        result = execute_portfolio_configuration(CONFIGURATION_UID)

    # The summary says whether the run reached the first rebalance (issue #41).
    assert first_rebalance.call_args.args == (resolved.rebalance_configuration,)
    assert first_rebalance.call_args.kwargs["starts_at"] == resolved.signal_start_time
    assert result.first_rebalance == first_rebalance.return_value
    # Its bars first (issue #39), then the released stages.
    bars_update.assert_called_once_with(resolved)
    start_engine.assert_called_once_with()
    assert manager.mock_calls[0].args == ()
    assert manager.mock_calls[0].kwargs == {"update_tree": False}
    assert manager.mock_calls[1].args == ()
    assert manager.mock_calls[1].kwargs == {"update_tree": False}
    assert manager.mock_calls[2].args == ()
    assert manager.mock_calls[2].kwargs == {"update_tree": False}
    assert manager.mock_calls[3].args == ()
    assert manager.mock_calls[3].kwargs == {"update_tree": False}
    assert manager.mock_calls[4].args == ()
    assert manager.mock_calls[4].kwargs == {
        "update_tree": False,
        "update_pointers": True,
    }
    portfolio_node.signal_weights.get_update_statistics.assert_called_once_with()
    signal_statistics.filter_identity_level.assert_called_once_with(
        level=1,
        filters=["signal-uid"],
    )
    assert portfolio_node.signal_weights.update_statistics is signal_statistics
    update_row.assert_called_once_with(
        CONFIGURATION_UID,
        values={"portfolio_uid": PORTFOLIO_UID},
    )
    # The run registers and reconciles the portfolio's Markets catalog row.
    register_datasets.assert_called_once_with(str(PORTFOLIO_UID))
    assert result.portfolio_uid == str(PORTFOLIO_UID)


def test_an_immediate_rebalance_builds_immediate_signal_and_no_calendar_events() -> None:
    """immediate_signal trades each set of weights at its own time and reads no calendar."""
    from msm_portfolios.rebalance_strategy import ImmediateSignal

    from alpaca_connectors.portfolios.execution import build_portfolio_graph

    def graph(strategy: str):
        rebalance = rebalance_configuration().model_copy(update={"strategy": strategy})
        resolved = resolved_configuration()
        resolved = ResolvedPortfolioConfiguration(
            **{
                **{name: getattr(resolved, name) for name in resolved.__slots__},
                "rebalance_configuration": rebalance,
            }
        )
        updater = Mock()
        updater.return_value.dependencies.return_value = {"portfolio_weights": Mock()}
        with (
            patch(
                "alpaca_connectors.portfolios.signal_definitions.weights_signal",
                return_value=Mock(signal_uid="signal-uid"),
            ),
            patch(
                "alpaca_connectors.portfolios.provided_weights.portfolio_signal_key",
                return_value="signal-key",
            ),
            patch(
                "alpaca_connectors.portfolios.execution.build_alpaca_interpolated_prices",
                return_value=Mock(),
            ),
            patch(
                "alpaca_connectors.portfolios.execution.ensure_trading_calendar",
                return_value=SimpleNamespace(uid="calendar-uid", unique_identifier="NYSE"),
            ),
            patch("msm_portfolios.time_index.PortfolioCalendarEvents") as calendar_events,
            patch("msm_portfolios.rebalance_strategy.CalendarEventSignal") as calendar_signal,
            patch("msm_portfolios.configuration.BacktestingWeightsConfig") as weights_config,
            patch("msm_portfolios.configuration.PortfolioBuildConfiguration"),
            patch("msm_portfolios.configuration.PortfolioConfiguration"),
            patch("msm_portfolios.configuration.PortfolioMarketsConfig"),
            patch("msm_portfolios.configuration.FrontEndDetails") as front_end,
            patch("msm_portfolios.time_index.PortfoliosUpdater", updater),
            patch("msm.api.portfolios.Portfolio.upsert", return_value=Mock()),
            patch("alpaca_connectors.instances.portfolio_output_tables", return_value={}),
        ):
            built = build_portfolio_graph(resolved)
        return SimpleNamespace(
            built=built,
            calendar_events=calendar_events,
            calendar_signal=calendar_signal,
            strategy=weights_config.call_args.kwargs["rebalance_strategy_instance"],
            name=front_end.call_args.kwargs["rebalance_strategy_name"],
        )

    immediate = graph("immediate_signal")
    assert type(immediate.strategy) is ImmediateSignal
    assert immediate.built.calendar_events is None
    immediate.calendar_events.assert_not_called()
    immediate.calendar_signal.assert_not_called()
    assert immediate.name == "ImmediateSignal"

    calendar = graph("calendar_event_signal")
    assert calendar.strategy is calendar.calendar_signal.return_value
    assert calendar.built.calendar_events is calendar.calendar_events.return_value
    assert calendar.calendar_signal.call_args.kwargs["calendar_events_instance"] is (
        calendar.calendar_events.return_value
    )
    assert calendar.calendar_signal.call_args.kwargs["rebalance_event"] == "market_close"


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
