from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.operations.signal_job_configurations import (
    AlpacaETFSignalJobConfiguration,
    AlpacaETFSignalJobConfigurationTable,
    normalize_schedule,
    validate_signal_job_references,
)
from src.operations.signal_jobs import (
    ALPACA_ETF_SIGNAL_EXECUTION_PATH,
    _create_platform_job,
    _ensure_signal_metadata,
    _patch_platform_job,
    execute_current_signal_job,
    launch_signal_job,
    reconcile_signal_job_configuration,
    resolve_current_signal_job_configuration,
)

CONFIGURATION_UID = uuid.UUID("11111111-1111-4111-8111-111111111111")
UNIVERSE_UID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ACCOUNT_UID = uuid.UUID("33333333-3333-4333-8333-333333333333")
JOB_UID = uuid.UUID("44444444-4444-4444-8444-444444444444")
JOB_RUN_UID = uuid.UUID("55555555-5555-4555-8555-555555555555")
ENVIRONMENT_UID = "66666666-6666-4666-8666-666666666666"


def configuration(**overrides) -> AlpacaETFSignalJobConfiguration:
    now = dt.datetime(2026, 9, 4, 9, tzinfo=dt.UTC)
    values = {
        "uid": CONFIGURATION_UID,
        "name": "Daily IVV observation",
        "description": "Observe the registered Universe.",
        "universe_uid": UNIVERSE_UID,
        "account_uid": ACCOUNT_UID,
        "job_uid": JOB_UID,
        "enabled": True,
        "schedule_type": "interval",
        "schedule_every": 1,
        "schedule_period": "days",
        "schedule_expression": None,
        "schedule_start_time": None,
        "cpu_request": "0.25",
        "memory_request": "0.5",
        "max_runtime_seconds": 3600,
        "spot": False,
        "lifecycle_state": "ready",
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return AlpacaETFSignalJobConfiguration.model_validate(values)


def test_configuration_is_one_job_per_universe_and_account_is_runtime_only() -> None:
    indexes = list(AlpacaETFSignalJobConfigurationTable.__table__.indexes)

    assert any(index.unique and [column.name for column in index.columns] == ["universe_uid"] for index in indexes)
    assert any(index.unique and [column.name for column in index.columns] == ["job_uid"] for index in indexes)
    assert AlpacaETFSignalJobConfigurationTable.__table__.c.account_uid.foreign_keys
    assert "environment_uid" not in AlpacaETFSignalJobConfigurationTable.__table__.c


def test_schedule_validation_keeps_exactly_one_schedule_shape() -> None:
    assert normalize_schedule(
        schedule_type="interval",
        schedule_every=2,
        schedule_period="hours",
    ) == {
        "schedule_type": "interval",
        "schedule_every": 2,
        "schedule_period": "hours",
        "schedule_expression": None,
        "schedule_start_time": None,
    }
    assert normalize_schedule(
        schedule_type="crontab",
        schedule_expression="0 9 * * 1-5",
    )["schedule_expression"] == "0 9 * * 1-5"
    assert normalize_schedule(
        schedule_type="crontab",
        schedule_expression="  */15   9-16 * * 1-5 ",
    )["schedule_expression"] == "*/15 9-16 * * 1-5"
    with pytest.raises(ValueError, match="five crontab fields"):
        normalize_schedule(schedule_type="crontab", schedule_expression="0 9 *")
    with pytest.raises(ValueError, match="minute must stay between 0 and 59"):
        normalize_schedule(schedule_type="crontab", schedule_expression="60 9 * * 1-5")
    with pytest.raises(ValueError, match="Invalid crontab hour step"):
        normalize_schedule(schedule_type="crontab", schedule_expression="0 */0 * * 1-5")


def test_signal_reference_validation_accepts_active_account_registration_mapping() -> None:
    universe = SimpleNamespace(is_active=True)

    with (
        patch("src.universes.get_asset_universe", return_value=universe),
        patch(
            "src.account.services.get_account_registration",
            return_value={"account_is_active": True},
        ),
    ):
        validate_signal_job_references(
            universe_uid=UNIVERSE_UID,
            account_uid=ACCOUNT_UID,
        )


def test_signal_reference_validation_rejects_inactive_account_registration_mapping() -> None:
    universe = SimpleNamespace(is_active=True)

    with (
        patch("src.universes.get_asset_universe", return_value=universe),
        patch(
            "src.account.services.get_account_registration",
            return_value={"account_is_active": False},
        ),
        pytest.raises(ValueError, match="is inactive"),
    ):
        validate_signal_job_references(
            universe_uid=UNIVERSE_UID,
            account_uid=ACCOUNT_UID,
        )


def test_platform_job_is_created_without_a_schedule_or_default_arguments() -> None:
    row = configuration(job_uid=None, lifecycle_state="provisioning")
    created = SimpleNamespace(uid=JOB_UID)

    with patch("mainsequence.client.Job.create", return_value=created) as create:
        assert _create_platform_job(row) is created

    create.assert_called_once_with(
        name="Alpaca ETF Signal — Daily IVV observation [11111111]",
        description=(
            "Observe the registered Universe. Configuration "
            "11111111-1111-4111-8111-111111111111; Universe "
            "22222222-2222-4222-8222-222222222222. The account is runtime-only "
            "and does not affect signal identity."
        ),
        execution_path=ALPACA_ETF_SIGNAL_EXECUTION_PATH,
        task_schedule=None,
        cpu_request="0.25",
        memory_request="0.5",
        spot=False,
        max_runtime_seconds=3600,
        automatic_deployment=True,
        timeout=60,
    )


def test_platform_job_patch_uses_sdk_8_1_interval_schedule_contract() -> None:
    row = configuration()
    job = Mock()
    patched = SimpleNamespace(
        uid=JOB_UID,
        task_schedule={
            "schedule": {"type": "interval", "every": 1, "period": "days"}
        },
    )
    job.patch.return_value = patched

    assert _patch_platform_job(row, job) is patched

    job.patch.assert_called_once_with(
        name="Alpaca ETF Signal — Daily IVV observation [11111111]",
        description=(
            "Observe the registered Universe. Configuration "
            "11111111-1111-4111-8111-111111111111; Universe "
            "22222222-2222-4222-8222-222222222222. The account is runtime-only "
            "and does not affect signal identity."
        ),
        cpu_request="0.25",
        memory_request="0.5",
        spot=False,
        max_runtime_seconds=3600,
        automatic_deployment=True,
        create_schedule=True,
        schedule={
            "schedule_type": "interval",
            "every": 1,
            "period": "days",
            "one_off": False,
        },
    )


def test_platform_job_patch_maps_crontab_expression_to_sdk_8_1_fields() -> None:
    starts_at = dt.datetime(2026, 9, 5, 8, tzinfo=dt.UTC)
    row = configuration(
        schedule_type="crontab",
        schedule_every=None,
        schedule_period=None,
        schedule_expression="15 9 * * 1-5",
        schedule_start_time=starts_at,
    )
    job = Mock()
    job.patch.return_value = SimpleNamespace(
        uid=JOB_UID,
        task_schedule={
            "schedule": {"type": "crontab", "expression": "15 9 * * 1-5"}
        },
    )

    _patch_platform_job(row, job)

    assert job.patch.call_args.kwargs["schedule"] == {
        "schedule_type": "crontab",
        "minute": "15",
        "hour": "9",
        "day_of_month": "*",
        "month_of_year": "*",
        "day_of_week": "1-5",
        "start_time": "2026-09-05T08:00:00+00:00",
        "one_off": False,
    }


def test_platform_job_patch_detaches_schedule_when_configuration_is_paused() -> None:
    row = configuration(enabled=False, lifecycle_state="paused")
    job = Mock()
    job.patch.return_value = SimpleNamespace(uid=JOB_UID, task_schedule=None)

    _patch_platform_job(row, job)

    assert job.patch.call_args.kwargs["create_schedule"] is False
    assert "schedule" not in job.patch.call_args.kwargs


def test_platform_job_patch_rejects_silently_ignored_enabled_schedule() -> None:
    row = configuration()
    job = Mock()
    job.patch.return_value = SimpleNamespace(uid=JOB_UID, task_schedule=None)

    with pytest.raises(RuntimeError, match="did not persist its enabled schedule"):
        _patch_platform_job(row, job)


def test_signal_metadata_is_materialized_without_publishing_weights() -> None:
    row = configuration(description="Observe the registered Universe.")
    signal = SimpleNamespace(signal_uid="signal-uid", get_explanation=Mock())

    with (
        patch(
            "src.portfolios.build_alpaca_etf_holdings_signal",
            return_value=signal,
        ) as build_signal,
        patch("msm_portfolios.api.market_metadata.SignalMetadata.upsert") as upsert,
    ):
        signal_uid = _ensure_signal_metadata(row)

    assert signal_uid == "signal-uid"
    build_signal.assert_called_once_with(
        universe_uid=str(UNIVERSE_UID),
        account_uid=str(ACCOUNT_UID),
    )
    upsert.assert_called_once_with(
        signal_uid="signal-uid",
        signal_description="Observe the registered Universe.",
    )
    signal.get_explanation.assert_not_called()


def test_reconciliation_links_the_job_before_activating_its_schedule() -> None:
    desired = configuration(job_uid=None, lifecycle_state="provisioning")
    linked = desired.model_copy(update={"job_uid": JOB_UID})
    ready = linked.model_copy(update={"lifecycle_state": "ready"})
    job = SimpleNamespace(uid=JOB_UID)

    with (
        patch("src.operations.signal_jobs._current_environment_uid"),
        patch("src.operations.signal_jobs._ensure_signal_metadata") as ensure_signal,
        patch("src.operations.signal_jobs.get_signal_job_configuration", return_value=desired),
        patch("src.operations.signal_jobs._create_platform_job", return_value=job) as create,
        patch("src.operations.signal_jobs._patch_platform_job", return_value=job) as patch_job,
        patch(
            "src.operations.signal_jobs.update_signal_job_configuration_row",
            side_effect=[linked, ready],
        ) as update_row,
    ):
        result = reconcile_signal_job_configuration(CONFIGURATION_UID)

    assert result is ready
    ensure_signal.assert_called_once_with(desired)
    create.assert_called_once_with(desired)
    assert update_row.call_args_list[0].kwargs["values"]["job_uid"] == JOB_UID
    patch_job.assert_called_once_with(linked, job)
    assert update_row.call_args_list[1].kwargs["values"]["lifecycle_state"] == "ready"


def test_manual_run_passes_no_business_arguments_to_job_run() -> None:
    row = configuration()
    job = Mock(uid=JOB_UID, image_status="ready")
    job.run_job.return_value = {"uid": str(JOB_RUN_UID), "status": "PENDING"}

    with (
        patch("src.operations.signal_jobs._current_environment_uid"),
        patch("src.operations.signal_jobs.get_signal_job_configuration", return_value=row),
        patch("src.operations.signal_jobs.validate_signal_job_references"),
        patch("src.operations.signal_jobs._require_linked_job", return_value=job),
        patch("src.operations.signal_jobs.list_signal_job_runs", return_value=[]),
    ):
        result = launch_signal_job(CONFIGURATION_UID)

    job.run_job.assert_called_once_with(timeout=60)
    assert result.job_run_uid == str(JOB_RUN_UID)


def test_runtime_resolves_configuration_from_job_run_and_job_uid() -> None:
    row = configuration()
    run = SimpleNamespace(
        uid=JOB_RUN_UID,
        job_uid=JOB_UID,
        organization_environment_uid=ENVIRONMENT_UID,
    )

    with (
        patch("mainsequence.client.JobRun.filter", return_value=[run]) as filter_runs,
        patch(
            "src.operations.signal_jobs.get_signal_job_configuration_by_job_uid",
            return_value=row,
        ) as get_by_job,
        patch(
            "src.operations.signal_jobs._current_environment_uid",
            return_value=ENVIRONMENT_UID,
        ),
    ):
        resolved, resolved_run = resolve_current_signal_job_configuration(
            job_run_uid=JOB_RUN_UID
        )

    assert resolved is row
    assert resolved_run is run
    filter_runs.assert_called_once_with(uid=str(JOB_RUN_UID), timeout=60)
    get_by_job.assert_called_once_with(JOB_UID)


def test_job_executes_one_universe_signal_update() -> None:
    row = configuration()
    run = SimpleNamespace(uid=JOB_RUN_UID, job_uid=JOB_UID)
    materialization = SimpleNamespace(
        signal_uid="signal-uid",
        observed_at=dt.datetime(2026, 9, 4, 10, tzinfo=dt.UTC),
        signal_weight_row_count=503,
        asset_uids={"AAPL": "asset-uid"},
    )

    with (
        patch(
            "src.operations.signal_jobs.resolve_current_signal_job_configuration",
            return_value=(row, run),
        ),
        patch("src.universes.run_asset_universe", return_value=materialization) as run_universe,
    ):
        result = execute_current_signal_job(job_run_uid=JOB_RUN_UID)

    run_universe.assert_called_once_with(UNIVERSE_UID, account_uid=ACCOUNT_UID)
    assert result["signal_uid"] == "signal-uid"
    assert result["signal_weight_row_count"] == 503
