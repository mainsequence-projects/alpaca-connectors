"""API shaping for branch-owned Main Sequence JobRun status."""

from __future__ import annotations

from src.operations import get_alpaca_bars_update_job_run, get_signal_job_run

from ..schemas import JobRunStatusResponse


def get_job_run(job_run_uid: str) -> JobRunStatusResponse | None:
    run = get_alpaca_bars_update_job_run(job_run_uid)
    configuration_uid = run.configuration_uid if run is not None else None
    command_args = run.command_args if run is not None else []
    if run is None:
        signal_result = get_signal_job_run(job_run_uid)
        if signal_result is None:
            return None
        configuration_uid, run = signal_result
    error = None
    if run.failure_message:
        error = {
            "code": "job_run_failed",
            "message": run.failure_message,
            "retryable": False,
        }
    return JobRunStatusResponse(
        uid=run.uid,
        job_uid=run.job_uid,
        job_name=run.job_name,
        configuration_uid=configuration_uid,
        status=run.status,
        execution_start=run.execution_start,
        execution_end=run.execution_end,
        commit_hash=run.commit_hash,
        runtime_image_uid=run.runtime_image_uid,
        runtime_image_digest=run.runtime_image_digest,
        command_args=command_args,
        logs_url=run.logs_url,
        error=error,
    )


__all__ = ["get_job_run"]
