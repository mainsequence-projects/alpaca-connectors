from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.operations.portfolio_jobs import (
    ALPACA_ETF_PORTFOLIO_EXECUTION_PATH,
    _create_platform_job,
    _patch_platform_job,
    launch_portfolio_job,
    normalize_portfolio_job_settings,
    resolve_current_portfolio_job_configuration,
)
from src.portfolios.configurations import (
    AlpacaETFPortfolioConfiguration,
    AlpacaETFPortfolioConfigurationTable,
    PortfolioRebalanceConfiguration,
    normalize_rebalance_strategy,
)
from src.portfolios.execution import (
    PortfolioExecutionGraph,
    ResolvedPortfolioConfiguration,
    execute_portfolio_configuration,
    resolve_portfolio_configuration,
)
from src.portfolios.signal_history import (
    SignalObservationAsset,
    SignalObservationBounds,
    SignalObservationMatrix,
)

CONFIGURATION_UID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SIGNAL_CONFIGURATION_UID = uuid.UUID("22222222-2222-4222-8222-222222222222")
BARS_CONFIGURATION_UID = uuid.UUID("33333333-3333-4333-8333-333333333333")
REBALANCE_CONFIGURATION_UID = uuid.UUID("44444444-4444-4444-8444-444444444444")
PORTFOLIO_UID = uuid.UUID("55555555-5555-4555-8555-555555555555")
JOB_UID = uuid.UUID("66666666-6666-4666-8666-666666666666")
JOB_RUN_UID = uuid.UUID("77777777-7777-4777-8777-777777777777")
ENVIRONMENT_UID = "88888888-8888-4888-8888-888888888888"


def portfolio_configuration(**overrides) -> AlpacaETFPortfolioConfiguration:
    now = dt.datetime(2026, 9, 5, 9, tzinfo=dt.UTC)
    values = {
        "uid": CONFIGURATION_UID,
        "name": "Daily IVV analytical portfolio",
        "description": "NYSE-close ETF portfolio backtest.",
        "signal_configuration_uid": SIGNAL_CONFIGURATION_UID,
        "bars_configuration_uid": BARS_CONFIGURATION_UID,
        "rebalance_configuration_uid": REBALANCE_CONFIGURATION_UID,
        "portfolio_uid": None,
        "job_uid": JOB_UID,
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
    return AlpacaETFPortfolioConfiguration.model_validate(values)


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
        signal_configuration=SimpleNamespace(
            uid=SIGNAL_CONFIGURATION_UID,
            name="Daily IVV signal",
            universe_uid=uuid.uuid4(),
            account_uid=uuid.uuid4(),
        ),
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


def test_portfolio_configuration_owns_calculation_fields_only() -> None:
    columns = AlpacaETFPortfolioConfigurationTable.__table__.c

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
        "account_uid",
        "source_time_index_meta_table_uid",
    ):
        assert forbidden not in columns
    assert columns.signal_configuration_uid.foreign_keys
    assert columns.bars_configuration_uid.foreign_keys
    assert columns.rebalance_configuration_uid.foreign_keys
    assert columns.portfolio_uid.foreign_keys
    assert any(
        index.unique and [column.name for column in index.columns] == ["job_uid"]
        for index in AlpacaETFPortfolioConfigurationTable.__table__.indexes
    )


def test_rebalance_accepts_only_calendar_event_signal() -> None:
    assert normalize_rebalance_strategy("CALENDAR_EVENT_SIGNAL") == "calendar_event_signal"
    with pytest.raises(ValueError, match="calendar_event_signal"):
        normalize_rebalance_strategy("time_weighted")


def test_portfolio_job_settings_are_transient_and_use_sdk_schedule_validation() -> None:
    settings = normalize_portfolio_job_settings(
        schedule_type="crontab",
        schedule_expression="0 8 * * 1-5",
        schedule_timezone="Europe/Vienna",
        cpu_request="0.5",
        memory_request="1",
        max_runtime_seconds=7200,
    )

    assert settings.schedule_expression == "0 8 * * 1-5"
    assert settings.schedule_timezone == "Europe/Vienna"
    assert settings.cpu_request == "0.5"
    assert settings.memory_request == "1"
    with pytest.raises(ValueError, match="five crontab fields"):
        normalize_portfolio_job_settings(
            schedule_type="crontab",
            schedule_expression="0 8 *",
        )


def test_portfolio_job_is_created_unscheduled_with_automatic_deployment() -> None:
    row = portfolio_configuration(job_uid=None)
    settings = normalize_portfolio_job_settings(
        schedule_type="interval",
        schedule_every=1,
        schedule_period="days",
    )
    created = SimpleNamespace(uid=JOB_UID)

    with patch("src.operations.platform_jobs.PlatformJob.create", return_value=created) as create:
        assert _create_platform_job(row, settings) is created

    assert create.call_args.kwargs == {
        "name": "Alpaca ETF Portfolio — Daily IVV analytical portfolio [11111111]",
        "description": (
            "NYSE-close ETF portfolio backtest. Portfolio configuration "
            "11111111-1111-4111-8111-111111111111; signal configuration "
            "22222222-2222-4222-8222-222222222222; bars configuration "
            "33333333-3333-4333-8333-333333333333."
        ),
        "execution_path": ALPACA_ETF_PORTFOLIO_EXECUTION_PATH,
        "task_schedule": None,
        "cpu_request": "0.25",
        "memory_request": "0.5",
        "spot": False,
        "max_runtime_seconds": 3600,
        "automatic_deployment": True,
        "timeout": 60,
    }


def test_portfolio_job_patch_writes_operational_settings_only_to_job() -> None:
    row = portfolio_configuration()
    settings = normalize_portfolio_job_settings(
        schedule_type="interval",
        schedule_every=1,
        schedule_period="days",
    )
    job = Mock()
    patched = SimpleNamespace(uid=JOB_UID, task_schedule={"schedule": {"type": "interval"}})
    job.patch.return_value = patched

    assert _patch_platform_job(row, job, settings) is patched
    assert job.patch.call_args.kwargs["schedule"] == {
        "schedule_type": "interval",
        "every": 1,
        "period": "days",
        "one_off": False,
    }
    assert job.patch.call_args.kwargs["automatic_deployment"] is True


def test_portfolio_job_patch_writes_crontab_timezone_only_to_job() -> None:
    row = portfolio_configuration()
    settings = normalize_portfolio_job_settings(
        schedule_type="crontab",
        schedule_expression="0 20 * * 1-5",
        schedule_timezone="America/New_York",
    )
    job = Mock()
    job.patch.return_value = SimpleNamespace(
        uid=JOB_UID,
        task_schedule={"schedule": {"type": "crontab"}},
    )

    _patch_platform_job(row, job, settings)

    assert job.patch.call_args.kwargs["schedule"] == {
        "schedule_type": "crontab",
        "minute": "0",
        "hour": "20",
        "day_of_month": "*",
        "month_of_year": "*",
        "day_of_week": "1-5",
        "timezone": "America/New_York",
        "one_off": False,
    }


def test_manual_portfolio_run_passes_no_business_arguments() -> None:
    row = portfolio_configuration()
    job = Mock(uid=JOB_UID, image_status="ready")
    job.run_job.return_value = {"uid": str(JOB_RUN_UID), "status": "PENDING"}

    with (
        patch("src.operations.portfolio_jobs._current_environment_uid"),
        patch("src.operations.portfolio_jobs.get_portfolio_configuration", return_value=row),
        patch("src.operations.portfolio_jobs._require_linked_job", return_value=job),
        patch("src.operations.portfolio_jobs.list_portfolio_job_runs", return_value=[]),
    ):
        result = launch_portfolio_job(CONFIGURATION_UID)

    job.run_job.assert_called_once_with(timeout=60)
    assert result.job_run_uid == str(JOB_RUN_UID)


def test_runtime_resolves_portfolio_configuration_from_owning_job() -> None:
    row = portfolio_configuration()
    run = SimpleNamespace(
        uid=JOB_RUN_UID,
        job_uid=JOB_UID,
        organization_environment_uid=ENVIRONMENT_UID,
    )

    with (
        patch("mainsequence.client.JobRun.filter", return_value=[run]),
        patch(
            "src.operations.portfolio_jobs.get_portfolio_configuration_by_job_uid",
            return_value=row,
        ) as get_by_job,
        patch(
            "src.operations.portfolio_jobs._current_environment_uid",
            return_value=ENVIRONMENT_UID,
        ),
    ):
        resolved, resolved_run = resolve_current_portfolio_job_configuration(
            job_run_uid=JOB_RUN_UID
        )

    assert resolved is row
    assert resolved_run is run
    get_by_job.assert_called_once_with(JOB_UID)


def test_portfolio_resolution_uses_published_signal_and_selected_bars_profile() -> None:
    row = portfolio_configuration()
    signal_configuration = SimpleNamespace(uid=SIGNAL_CONFIGURATION_UID)
    bars_configuration = SimpleNamespace(
        uid=BARS_CONFIGURATION_UID,
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
        patch("src.portfolios.execution.get_portfolio_configuration", return_value=row),
        patch(
            "src.portfolios.execution.validate_portfolio_references",
            return_value=(signal_configuration, bars_configuration, rebalance),
        ),
        patch("src.operations.signal_uid_for_configuration", return_value="signal-uid"),
        patch(
            "src.portfolios.execution.read_signal_observation_matrix",
            return_value=matrix,
        ) as read_signal,
        patch(
            "src.portfolios.execution.read_signal_observation_bounds",
            return_value=bounds,
        ) as read_bounds,
        patch(
            "src.portfolios.execution.resolve_alpaca_bars_time_index_meta_table_uid",
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
        unique_identifier="ALPACA_ETF_PORTFOLIO__11111111111141118111111111111111",
    )
    portfolio_row = portfolio_node.target_portfolio
    manager = Mock()
    manager.attach_mock(valuation_source.run, "interpolation")
    manager.attach_mock(calendar_events.run, "calendar_events")
    manager.attach_mock(portfolio_rebalance.run, "rebalance")
    manager.attach_mock(portfolio_weights.run, "portfolio_weights")
    manager.attach_mock(portfolio_node.run, "portfolio")

    with (
        patch("src.runtime.start_portfolio_job_engine") as start_engine,
        patch(
            "src.portfolios.execution.resolve_portfolio_configuration",
            return_value=resolved,
        ),
        patch(
            "src.portfolios.execution.build_portfolio_graph",
            return_value=PortfolioExecutionGraph(
                valuation_source=valuation_source,
                calendar_events=calendar_events,
                portfolio_rebalance=portfolio_rebalance,
                portfolio_weights=portfolio_weights,
                portfolio_node=portfolio_node,
                portfolio_row=portfolio_row,
            ),
        ),
        patch("src.portfolios.execution.update_portfolio_configuration_row") as update_row,
    ):
        result = execute_portfolio_configuration(CONFIGURATION_UID)

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
    assert result.portfolio_uid == str(PORTFOLIO_UID)
