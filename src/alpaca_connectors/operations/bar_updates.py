"""Main Sequence Job orchestration for stored Alpaca bars configurations.

`.mainsequence/workflows/alpaca-bars-update.yaml` declares the `Alpaca Bars Update` Job, whose
runs each execute one configuration (``--configuration-uid <uid>``), and its dispatcher, which holds
the daily schedule (ADR 0021). The dispatcher's scheduled run carries ``--scheduled`` and starts one
run of the bars Job per enabled configuration, through the ``runner`` policy it declares on that
Job: a Job cannot declare access to itself. No code here creates a Job or changes its schedule.
"""

from __future__ import annotations

import argparse
import datetime as dt
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from alpaca_connectors.operations.configuration_jobs import (
    RUN_SCAN_LIMIT,
    RUNNING_JOB_STATUSES,
    SCHEDULED_ARGUMENT,
    configuration_uid_from_command_args,
)

ALPACA_BARS_UPDATE_JOB_NAME = "Alpaca Bars Update"
ALPACA_BARS_UPDATE_EXECUTION_PATH = "src/jobs/run_alpaca_bars_update.py"
ALPACA_BARS_UPDATE_DISPATCHER_JOB_NAME = "Alpaca Bars Update Dispatcher"
ALPACA_BARS_UPDATE_DISPATCHER_EXECUTION_PATH = "src/jobs/dispatch_alpaca_bars_update.py"
ALPACA_BARS_WORKFLOW_PATH = ".mainsequence/workflows/alpaca-bars-update.yaml"


@dataclass(frozen=True, slots=True)
class AlpacaBarsUpdateSubmission:
    configuration_uid: str
    job_uid: str
    job_run_uid: str
    status: str


@dataclass(frozen=True, slots=True)
class AlpacaBarsUpdateJobRun:
    uid: str
    job_uid: str
    job_name: str
    configuration_uid: str | None
    status: str
    execution_start: dt.datetime | None
    execution_end: dt.datetime | None
    commit_hash: str | None
    runtime_image_uid: str | None
    runtime_image_digest: str | None
    command_args: list[str]
    logs_url: str | None
    failure_message: str | None


def _canonical_uid(value: uuid.UUID | str, *, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid UUID.") from exc


def _normalized_status(value: Any) -> str:
    raw = getattr(value, "value", value)
    status = str(raw or "queued").strip().upper()
    aliases = {
        "COMPLETE": "SUCCEEDED",
        "COMPLETED": "SUCCEEDED",
        "SUCCESS": "SUCCEEDED",
        "SUCCESSFUL": "SUCCEEDED",
        "ERROR": "FAILED",
        "ERRORED": "FAILED",
    }
    return aliases.get(status, status)


def _job_image_is_ready(job: Any) -> bool:
    value = getattr(job.image_status, "value", job.image_status)
    return str(value or "").strip().lower() == "ready"


def _resolve_update_job() -> Any:
    from mainsequence.client import Job

    jobs = list(Job.filter(name=ALPACA_BARS_UPDATE_JOB_NAME, timeout=60))
    if not jobs:
        raise ValueError(
            f"The {ALPACA_BARS_UPDATE_JOB_NAME!r} Job has not been deployed for this branch; a "
            f"push of {ALPACA_BARS_WORKFLOW_PATH} creates it."
        )
    if len(jobs) > 1:
        raise RuntimeError(f"More than one branch Job is named {ALPACA_BARS_UPDATE_JOB_NAME!r}.")
    job = jobs[0]
    if job.execution_path != ALPACA_BARS_UPDATE_EXECUTION_PATH:
        raise RuntimeError(
            f"The {ALPACA_BARS_UPDATE_JOB_NAME!r} Job does not use the reviewed launcher."
        )
    if not _job_image_is_ready(job):
        raise ValueError(f"The {ALPACA_BARS_UPDATE_JOB_NAME!r} Job image is not ready.")
    return job


def launch_alpaca_bars_update(
    configuration_uid: uuid.UUID | str,
) -> AlpacaBarsUpdateSubmission:
    """Preflight one stored configuration and enqueue the canonical branch Job."""
    from alpaca_connectors.market_data import resolve_market_data_update

    canonical_configuration_uid = _canonical_uid(
        configuration_uid,
        label="Configuration UID",
    )
    resolve_market_data_update(configuration_uid=canonical_configuration_uid)
    return _start_update(_resolve_update_job(), canonical_configuration_uid)


def _start_update(job: Any, configuration_uid: str) -> AlpacaBarsUpdateSubmission:
    payload = job.run_job(
        timeout=60,
        command_args=["--configuration-uid", configuration_uid],
    )
    job_run_uid = payload.get("uid") or payload.get("job_run_uid")
    if not job_run_uid:
        raise RuntimeError("Main Sequence accepted the Job request without returning a JobRun UID.")
    return AlpacaBarsUpdateSubmission(
        configuration_uid=configuration_uid,
        job_uid=str(job.uid),
        job_run_uid=str(job_run_uid),
        status=_normalized_status(payload.get("status")),
    )


def _configuration_uid_from_args(command_args: list[str]) -> str | None:
    try:
        option_index = command_args.index("--configuration-uid")
        value = command_args[option_index + 1]
    except (ValueError, IndexError):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def get_alpaca_bars_update_job_run(
    job_run_uid: uuid.UUID | str,
) -> AlpacaBarsUpdateJobRun | None:
    """Return a sanitized projection of one generic Alpaca bars JobRun."""
    from mainsequence.client import JobRun

    canonical_job_run_uid = _canonical_uid(job_run_uid, label="JobRun UID")
    runs = list(JobRun.filter(uid=canonical_job_run_uid, timeout=60))
    if not runs:
        return None
    if len(runs) > 1:
        raise RuntimeError(f"More than one JobRun resolved for UID {canonical_job_run_uid!r}.")
    run = runs[0]
    if run.job_name != ALPACA_BARS_UPDATE_JOB_NAME:
        return None
    command_args = [str(value) for value in (run.command_args or [])]
    observability = run.observability
    status = _normalized_status(run.status)
    return AlpacaBarsUpdateJobRun(
        uid=str(run.uid),
        job_uid=str(run.job_uid),
        job_name=str(run.job_name),
        configuration_uid=_configuration_uid_from_args(command_args),
        status=status,
        execution_start=run.execution_start,
        execution_end=run.execution_end,
        commit_hash=run.commit_hash,
        runtime_image_uid=(str(run.runtime_image_uid) if run.runtime_image_uid else None),
        runtime_image_digest=run.runtime_image_digest,
        command_args=command_args,
        logs_url=(observability.application_logs_url if observability else None),
        failure_message=("The Alpaca bars update failed." if status == "FAILED" else None),
    )


def _recent_update_runs(job_uid: Any) -> Iterator[Any]:
    from mainsequence.client import JobRun

    return JobRun.iter_filter(job__uid=str(job_uid), max_items=RUN_SCAN_LIMIT, timeout=60)


def _active_configuration_uids(job: Any) -> set[str]:
    active: set[str] = set()
    for run in _recent_update_runs(job.uid):
        configuration_uid = configuration_uid_from_command_args(run.command_args)
        if configuration_uid is not None and _normalized_status(run.status) in (
            RUNNING_JOB_STATUSES
        ):
            active.add(configuration_uid)
    return active


def dispatch_scheduled_bar_updates() -> dict[str, Any]:
    """Start one bars Job run per enabled configuration: all the dispatcher does (ADR 0021).

    The dispatcher's scheduled run calls this; it holds ``runner`` on the bars Job. A
    configuration with a pending or running run is skipped. There is no preflight: each run
    resolves its configuration and records its own failure. One failed start does not stop the
    others.
    """
    from alpaca_connectors.market_data import scheduled_bar_configuration_uids

    job = _resolve_update_job()
    configuration_uids = [
        _canonical_uid(value, label="Configuration UID")
        for value in scheduled_bar_configuration_uids()
    ]
    active = _active_configuration_uids(job)
    launched: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    for configuration_uid in configuration_uids:
        if configuration_uid in active:
            skipped.append(
                {"configuration_uid": configuration_uid, "reason": "pending or running JobRun"}
            )
            continue
        # One configuration's failure must not stop the others.
        try:
            submission = _start_update(job, configuration_uid)
        except Exception as exc:
            failed.append({"configuration_uid": configuration_uid, "error": repr(exc)})
            continue
        launched.append(
            {"configuration_uid": configuration_uid, "job_run_uid": submission.job_run_uid}
        )
    return {
        "kind": "bars",
        "job_uid": str(job.uid),
        "scheduled_configurations": len(configuration_uids),
        "launched": launched,
        "skipped": skipped,
        "failed": failed,
    }


def build_bars_dispatcher_argument_parser() -> argparse.ArgumentParser:
    """Arguments of the bars dispatcher: its scheduled run's ``--scheduled`` and nothing else."""
    parser = argparse.ArgumentParser(
        prog=ALPACA_BARS_UPDATE_DISPATCHER_EXECUTION_PATH,
        description=(
            f"Start one {ALPACA_BARS_UPDATE_JOB_NAME!r} run per enabled stored bar configuration."
        ),
    )
    parser.add_argument(
        SCHEDULED_ARGUMENT,
        action="store_true",
        required=True,
        help="Start one run per enabled bar configuration (the dispatcher's scheduled run).",
    )
    return parser


def run_bars_dispatcher_job(argv: Sequence[str] | None = None) -> dict[str, Any]:
    """Entry point of the bars dispatcher Job: the scheduled dispatch."""
    build_bars_dispatcher_argument_parser().parse_args(argv)
    return dispatch_scheduled_bar_updates()


__all__ = [
    "ALPACA_BARS_UPDATE_DISPATCHER_EXECUTION_PATH",
    "ALPACA_BARS_UPDATE_DISPATCHER_JOB_NAME",
    "ALPACA_BARS_UPDATE_EXECUTION_PATH",
    "ALPACA_BARS_UPDATE_JOB_NAME",
    "ALPACA_BARS_WORKFLOW_PATH",
    "AlpacaBarsUpdateJobRun",
    "AlpacaBarsUpdateSubmission",
    "build_bars_dispatcher_argument_parser",
    "dispatch_scheduled_bar_updates",
    "get_alpaca_bars_update_job_run",
    "launch_alpaca_bars_update",
    "run_bars_dispatcher_job",
]
