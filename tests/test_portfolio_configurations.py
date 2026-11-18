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
        patch.object(execution, "get_portfolio_configuration", return_value=row),
        patch.object(
            execution,
            "resolve_portfolio_configuration",
            side_effect=ValueError("Signal x has no published observations."),
        ) as resolve,
    ):
        blocked = preflight_portfolio_configuration(CONFIGURATION_UID)
    with (
        patch.object(execution, "get_portfolio_configuration", return_value=row),
        patch.object(execution, "resolve_portfolio_configuration") as resolve_allowed,
    ):
        allowed = preflight_portfolio_configuration(CONFIGURATION_UID)

    resolve.assert_called_once_with(CONFIGURATION_UID)
    resolve_allowed.assert_called_once_with(CONFIGURATION_UID)
    assert blocked == PortfolioPreflight(
        configuration_uid=str(CONFIGURATION_UID),
        blockers=("Signal x has no published observations.",),
    )
    assert not blocked.allowed
    assert allowed.allowed


def test_preflight_of_a_missing_configuration_is_not_found() -> None:
    with (
        patch.object(execution, "get_portfolio_configuration", return_value=None),
        pytest.raises(LookupError, match="does not exist"),
    ):
        preflight_portfolio_configuration(CONFIGURATION_UID)


def test_portfolio_resolution_uses_published_signal_and_selected_bars_profile() -> None:
    row = portfolio_configuration()
    bars_configuration = SimpleNamespace(
        uid=BARS_CONFIGURATION_UID,
        enabled=True,
        frequency_id="1d",
        feed="sip",
        adjustment="all",
    )
    rebalance = rebalance_configuration()
    matrix = SignalObservationMatrix(
        signal_uid="signal-uid",
        time_indexes=(dt.datetime(2026, 9, 5, 8, tzinfo=dt.UTC),),
        assets=(
            SignalObservationAsset("ALPACA::asset-b", "B", None, (0.4,)),
            SignalObservationAsset("ALPACA::asset-a", "A", None, (0.6,)),
        ),
    )
    bounds = SignalObservationBounds(
        signal_uid="signal-uid",
        first_observation_at=dt.datetime(2026, 9, 3, 8, tzinfo=dt.UTC),
        last_observation_at=dt.datetime(2026, 9, 5, 8, tzinfo=dt.UTC),
    )

    with (
        patch(
            "alpaca_connectors.portfolios.execution.get_portfolio_configuration", return_value=row
        ),
        patch(
            "alpaca_connectors.portfolios.execution.validate_portfolio_references",
            return_value=rebalance,
        ),
        patch(
            "alpaca_connectors.market_data.configurations.portfolio_bar_configuration",
            return_value=bars_configuration,
        ),
        patch(
            "alpaca_connectors.portfolios.provided_weights.portfolio_signal_uid",
            return_value="signal-uid",
        ),
        patch(
            "alpaca_connectors.portfolios.signal_assets.assets_for_signals",
            return_value=["ALPACA::asset-a"],
        ),
        patch(
            "alpaca_connectors.portfolios.execution.read_signal_observation_matrix",
            return_value=matrix,
        ) as read_signal,
        patch(
            "alpaca_connectors.portfolios.execution.read_signal_observation_bounds",
            return_value=bounds,
        ) as read_bounds,
        patch(
            "alpaca_connectors.portfolios.execution.resolve_alpaca_bars_time_index_meta_table_uid",
            return_value="source-table-uid",
        ) as resolve_bars,
    ):
        resolved = resolve_portfolio_configuration(CONFIGURATION_UID)

    assert resolved.asset_identifiers == ("ALPACA::asset-a", "ALPACA::asset-b")
    assert resolved.signal_start_time == bounds.first_observation_at
    read_signal.assert_called_once_with(signal_uid="signal-uid", observation_limit=1)
    read_bounds.assert_called_once_with(signal_uid="signal-uid")
    resolve_bars.assert_called_once_with(frequency_id="1d", feed="sip", adjustment="all")


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
    ):
        result = execute_portfolio_configuration(CONFIGURATION_UID)

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
