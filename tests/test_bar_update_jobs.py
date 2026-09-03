from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.operations.bar_updates import (
    ALPACA_BARS_UPDATE_EXECUTION_PATH,
    ALPACA_BARS_UPDATE_JOB_NAME,
    get_alpaca_bars_update_job_run,
    launch_alpaca_bars_update,
)

CONFIGURATION_UID = "11111111-1111-4111-8111-111111111111"
JOB_UID = "22222222-2222-4222-8222-222222222222"
JOB_RUN_UID = "33333333-3333-4333-8333-333333333333"


def _ready_job() -> SimpleNamespace:
    return SimpleNamespace(
        uid=JOB_UID,
        name=ALPACA_BARS_UPDATE_JOB_NAME,
        execution_path=ALPACA_BARS_UPDATE_EXECUTION_PATH,
        image_status="READY",
        run_job=Mock(return_value={"uid": JOB_RUN_UID, "status": "PENDING"}),
    )


def test_launch_preflights_and_enqueues_only_configuration_uid() -> None:
    job = _ready_job()
    with (
        patch("src.market_data.resolve_market_data_update") as resolve,
        patch("mainsequence.client.Job.filter", return_value=[job]) as job_filter,
    ):
        submission = launch_alpaca_bars_update(CONFIGURATION_UID)

    resolve.assert_called_once_with(configuration_uid=CONFIGURATION_UID)
    job_filter.assert_called_once_with(name=ALPACA_BARS_UPDATE_JOB_NAME, timeout=60)
    job.run_job.assert_called_once_with(
        timeout=60,
        command_args=["--configuration-uid", CONFIGURATION_UID],
    )
    assert submission.configuration_uid == CONFIGURATION_UID
    assert submission.job_uid == JOB_UID
    assert submission.job_run_uid == JOB_RUN_UID
    assert submission.status == "PENDING"


@pytest.mark.parametrize(
    ("jobs", "message"),
    [
        ([], "has not been deployed"),
        ([_ready_job(), _ready_job()], "More than one branch Job"),
    ],
)
def test_launch_requires_exactly_one_branch_job(jobs: list, message: str) -> None:
    with (
        patch("src.market_data.resolve_market_data_update"),
        patch("mainsequence.client.Job.filter", return_value=jobs),
        pytest.raises((ValueError, RuntimeError), match=message),
    ):
        launch_alpaca_bars_update(CONFIGURATION_UID)


def test_launch_rejects_non_ready_job() -> None:
    job = _ready_job()
    job.image_status = "BUILDING"
    with (
        patch("src.market_data.resolve_market_data_update"),
        patch("mainsequence.client.Job.filter", return_value=[job]),
        pytest.raises(ValueError, match="image is not ready"),
    ):
        launch_alpaca_bars_update(CONFIGURATION_UID)

    job.run_job.assert_not_called()


def test_launch_rejects_unreviewed_execution_path() -> None:
    job = _ready_job()
    job.execution_path = "src/jobs/something_else.py"
    with (
        patch("src.market_data.resolve_market_data_update"),
        patch("mainsequence.client.Job.filter", return_value=[job]),
        pytest.raises(RuntimeError, match="reviewed launcher"),
    ):
        launch_alpaca_bars_update(CONFIGURATION_UID)

    job.run_job.assert_not_called()


def test_job_run_projection_is_pollable_and_sanitized() -> None:
    now = datetime.now(UTC)
    run = SimpleNamespace(
        uid=JOB_RUN_UID,
        job_uid=JOB_UID,
        job_name=ALPACA_BARS_UPDATE_JOB_NAME,
        command_args=["--configuration-uid", CONFIGURATION_UID],
        status="ERRORED",
        execution_start=now,
        execution_end=now,
        commit_hash="abc123",
        runtime_image_uid="44444444-4444-4444-8444-444444444444",
        runtime_image_digest="sha256:123",
        observability=SimpleNamespace(application_logs_url="https://logs.example/run"),
        response_error={"message": "private provider exception"},
    )
    with patch("mainsequence.client.JobRun.filter", return_value=[run]) as job_run_filter:
        result = get_alpaca_bars_update_job_run(JOB_RUN_UID)

    job_run_filter.assert_called_once_with(uid=JOB_RUN_UID, timeout=60)
    assert result is not None
    assert result.configuration_uid == CONFIGURATION_UID
    assert result.status == "FAILED"
    assert result.logs_url == "https://logs.example/run"
    assert result.failure_message == "The Alpaca bars update failed."
    assert "private provider exception" not in repr(result)


def test_job_run_projection_hides_unrelated_jobs() -> None:
    run = SimpleNamespace(job_name="Some Other Job")
    with patch("mainsequence.client.JobRun.filter", return_value=[run]):
        assert get_alpaca_bars_update_job_run(JOB_RUN_UID) is None
