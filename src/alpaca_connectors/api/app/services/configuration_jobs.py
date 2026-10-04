"""API shaping of the workflow-declared signal and portfolio Jobs (ADR 0013).

Configuration responses report the kind's declared Job as ``job_uid`` and ``job`` (its live
schedule, compute and image). Both are null until a push creates the Job on the branch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from ..schemas import ConfigurationJobResponse


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def schedule_payload(job: Any) -> dict[str, Any]:
    task_schedule = getattr(job, "task_schedule", None)
    schedule = _value(task_schedule, "schedule") if task_schedule is not None else None
    if schedule is None:
        return {
            "schedule_type": None,
            "schedule_every": None,
            "schedule_period": None,
            "schedule_expression": None,
            "schedule_timezone": None,
            "schedule_timezone_explicit": None,
            "schedule_start_time": None,
        }
    schedule_type = str(_value(schedule, "type", _value(schedule, "schedule_type", ""))).lower()
    if schedule_type == "interval":
        return {
            "schedule_type": "interval",
            "schedule_every": _value(schedule, "every"),
            "schedule_period": _value(schedule, "period"),
            "schedule_expression": None,
            "schedule_timezone": None,
            "schedule_timezone_explicit": None,
            "schedule_start_time": _value(schedule, "start_time"),
        }
    expression = _value(schedule, "expression")
    if not expression:
        expression = " ".join(
            str(_value(schedule, key, "*"))
            for key in ("minute", "hour", "day_of_month", "month_of_year", "day_of_week")
        )
    return {
        "schedule_type": "crontab",
        "schedule_every": None,
        "schedule_period": None,
        "schedule_expression": expression,
        "schedule_timezone": _value(schedule, "timezone"),
        "schedule_timezone_explicit": _value(schedule, "timezone_explicit"),
        "schedule_start_time": _value(schedule, "start_time"),
    }


def job_response(job: Any | None) -> ConfigurationJobResponse | None:
    if job is None:
        return None
    image_status = getattr(job, "image_status", None)
    image_status = getattr(image_status, "value", image_status)
    return ConfigurationJobResponse(
        uid=str(job.uid),
        **schedule_payload(job),
        cpu_request=(str(job.cpu_request) if job.cpu_request is not None else None),
        memory_request=(str(job.memory_request) if job.memory_request is not None else None),
        max_runtime_seconds=job.max_runtime_seconds,
        spot=bool(job.spot),
        image_status=(str(image_status) if image_status is not None else None),
        automatic_deployment=bool(job.automatic_deployment),
    )


def job_fields(job: Any | None, latest_run: Any | None) -> dict[str, Any]:
    """The shared Job and a configuration's newest run, as response fields."""
    return {
        "job_uid": str(job.uid) if job is not None else None,
        "job": job_response(job),
        "latest_run_status": latest_run.status if latest_run is not None else None,
        "latest_run_at": latest_run.execution_start if latest_run is not None else None,
    }


def lifecycle_state(job: Any | None, *, scheduled: bool) -> str:
    """``provisioning`` before the push creates the Job, then ``ready`` or ``paused``."""
    if job is None:
        return "provisioning"
    if scheduled and getattr(job, "task_schedule", None):
        return "ready"
    return "paused"


def accepted_run(submission: Any) -> dict[str, Any]:
    """A started run as the 202 body: the submission plus the JobRun status URL."""
    payload = asdict(submission)
    return {**payload, "status_url": f"/v1/operations/job-runs/{payload['job_run_uid']}"}


__all__ = [
    "accepted_run",
    "job_fields",
    "job_response",
    "lifecycle_state",
    "schedule_payload",
]
