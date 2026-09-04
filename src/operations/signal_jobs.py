"""Main Sequence Job projection and execution for Alpaca ETF signals."""

from __future__ import annotations

import datetime as dt
import os
import uuid
from dataclasses import dataclass
from typing import Any

from src.operations.signal_job_configurations import (
    AlpacaETFSignalJobConfiguration,
    create_signal_job_configuration_row,
    delete_signal_job_configuration_row,
    get_signal_job_configuration,
    get_signal_job_configuration_by_job_uid,
    normalize_schedule,
    signal_job_configurations_for_universe,
    update_signal_job_configuration_row,
    validate_signal_job_references,
)

ALPACA_ETF_SIGNAL_EXECUTION_PATH = "src/jobs/run_alpaca_etf_signal.py"
RUNNING_JOB_STATUSES = frozenset({"PENDING", "QUEUED", "RUNNING", "STARTED"})
_UNSET = object()


@dataclass(frozen=True, slots=True)
class SignalJobSubmission:
    configuration_uid: str
    job_uid: str
    job_run_uid: str
    status: str


@dataclass(frozen=True, slots=True)
class SignalJobRun:
    uid: str
    job_uid: str
    job_name: str
    status: str
    execution_start: Any | None
    execution_end: Any | None
    commit_hash: str | None
    runtime_image_uid: str | None
    runtime_image_digest: str | None
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
    value = getattr(job, "image_status", None)
    value = getattr(value, "value", value)
    return str(value or "").strip().lower() == "ready"


def _current_environment_uid() -> str:
    from mainsequence.code_repository_context import resolve_organization_environment_uid

    return resolve_organization_environment_uid("Alpaca ETF signal Job operation")


def signal_uid_for_configuration(
    configuration: AlpacaETFSignalJobConfiguration,
) -> str:
    """Resolve the canonical Signal identity without publishing observations."""
    from src.portfolios import build_alpaca_etf_holdings_signal

    return build_alpaca_etf_holdings_signal(
        universe_uid=str(configuration.universe_uid),
        account_uid=str(configuration.account_uid),
    ).signal_uid


def _ensure_signal_metadata(configuration: AlpacaETFSignalJobConfiguration) -> str:
    """Materialize the real ms-markets Signal identity before provisioning its Job."""
    from msm_portfolios.api.market_metadata import SignalMetadata

    from src.portfolios import build_alpaca_etf_holdings_signal

    signal = build_alpaca_etf_holdings_signal(
        universe_uid=str(configuration.universe_uid),
        account_uid=str(configuration.account_uid),
    )
    SignalMetadata.upsert(
        signal_uid=signal.signal_uid,
        signal_description=configuration.description or signal.get_explanation(),
    )
    return signal.signal_uid


def _normalize_compute(
    *,
    cpu_request: str | int | float,
    memory_request: str | int | float,
) -> tuple[str, str]:
    from mainsequence.client.compute_validation import validate_and_normalize_compute_fields

    values = validate_and_normalize_compute_fields(
        cpu_request=cpu_request,
        memory_request=memory_request,
        gpu_request=None,
        gpu_type=None,
        require_cpu_and_memory=True,
        output_format="decimal",
    )
    return str(values["cpu_request"]), str(values["memory_request"])


def _job_name(configuration: AlpacaETFSignalJobConfiguration) -> str:
    suffix = str(configuration.uid).split("-", 1)[0]
    prefix = f"Alpaca ETF Signal — {configuration.name}".strip()
    return f"{prefix[:220]} [{suffix}]"


def _job_description(configuration: AlpacaETFSignalJobConfiguration) -> str:
    detail = configuration.description or "Scheduled Universe holdings observation."
    return (
        f"{detail} Configuration {configuration.uid!s}; Universe "
        f"{configuration.universe_uid!s}. The account is runtime-only and does not affect "
        "signal identity."
    )


def _job_schedule_update(configuration: AlpacaETFSignalJobConfiguration) -> dict[str, Any]:
    """Build the SDK 8.1 Job PATCH schedule contract."""
    if configuration.schedule_type == "interval":
        schedule: dict[str, Any] = {
            "schedule_type": "interval",
            "every": configuration.schedule_every,
            "period": configuration.schedule_period,
        }
    else:
        minute, hour, day_of_month, month_of_year, day_of_week = str(
            configuration.schedule_expression
        ).split()
        schedule = {
            "schedule_type": "crontab",
            "minute": minute,
            "hour": hour,
            "day_of_month": day_of_month,
            "month_of_year": month_of_year,
            "day_of_week": day_of_week,
        }
    if configuration.schedule_start_time is not None:
        schedule["start_time"] = configuration.schedule_start_time.isoformat()
    schedule["one_off"] = False
    return schedule


def _get_job(job_uid: uuid.UUID | str) -> Any | None:
    from mainsequence.client import Job

    rows = list(Job.filter(uid=_canonical_uid(job_uid, label="Job UID"), timeout=60))
    if len(rows) > 1:
        raise RuntimeError(f"More than one Job resolved for UID {job_uid!s}.")
    return rows[0] if rows else None


def _require_linked_job(configuration: AlpacaETFSignalJobConfiguration) -> Any:
    if configuration.job_uid is None:
        raise ValueError(
            f"Signal Job configuration {configuration.uid!s} has not provisioned a Job yet."
        )
    job = _get_job(configuration.job_uid)
    if job is None:
        raise ValueError(
            f"Main Sequence Job {configuration.job_uid!s} linked to configuration "
            f"{configuration.uid!s} no longer exists. Reconcile the configuration."
        )
    if job.execution_path != ALPACA_ETF_SIGNAL_EXECUTION_PATH:
        raise RuntimeError(
            f"Job {job.uid!s} linked to signal configuration {configuration.uid!s} does not use "
            "the reviewed signal launcher."
        )
    return job


def _create_platform_job(configuration: AlpacaETFSignalJobConfiguration) -> Any:
    from mainsequence.client import Job

    return Job.create(
        name=_job_name(configuration),
        description=_job_description(configuration),
        execution_path=ALPACA_ETF_SIGNAL_EXECUTION_PATH,
        task_schedule=None,
        cpu_request=configuration.cpu_request,
        memory_request=configuration.memory_request,
        spot=configuration.spot,
        max_runtime_seconds=configuration.max_runtime_seconds,
        automatic_deployment=True,
        timeout=60,
    )


def _patch_platform_job(configuration: AlpacaETFSignalJobConfiguration, job: Any) -> Any:
    values: dict[str, Any] = {
        "name": _job_name(configuration),
        "description": _job_description(configuration),
        "cpu_request": configuration.cpu_request,
        "memory_request": configuration.memory_request,
        "spot": configuration.spot,
        "max_runtime_seconds": configuration.max_runtime_seconds,
        "automatic_deployment": True,
        "create_schedule": configuration.enabled,
    }
    if configuration.enabled:
        values["schedule"] = _job_schedule_update(configuration)
    patched = job.patch(**values)
    actual_schedule = getattr(patched, "task_schedule", None)
    if configuration.enabled and actual_schedule is None:
        raise RuntimeError(
            f"Main Sequence Job {patched.uid!s} did not persist its enabled schedule."
        )
    if not configuration.enabled and actual_schedule is not None:
        raise RuntimeError(
            f"Main Sequence Job {patched.uid!s} did not detach its paused schedule."
        )
    return patched


def _record_reconciliation_error(
    configuration_uid: uuid.UUID | str,
    *,
    operation: str,
) -> None:
    update_signal_job_configuration_row(
        configuration_uid,
        values={
            "lifecycle_state": "error",
            "last_error": f"Main Sequence Job {operation} failed. Retry reconciliation.",
        },
    )


def reconcile_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaETFSignalJobConfiguration:
    """Converge one durable configuration into exactly one branch-owned Job."""
    _current_environment_uid()
    configuration = get_signal_job_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Signal Job configuration {configuration_uid!s} does not exist.")
    try:
        _ensure_signal_metadata(configuration)
        job = _get_job(configuration.job_uid) if configuration.job_uid is not None else None
        if job is None:
            job = _create_platform_job(configuration)
            if not getattr(job, "uid", None):
                raise RuntimeError("Main Sequence created a Job without returning its UID.")
            configuration = update_signal_job_configuration_row(
                configuration.uid,
                values={"job_uid": uuid.UUID(str(job.uid)), "last_error": None},
            )
        _patch_platform_job(configuration, job)
    except Exception:
        _record_reconciliation_error(configuration.uid, operation="reconciliation")
        raise
    return update_signal_job_configuration_row(
        configuration.uid,
        values={
            "lifecycle_state": "ready" if configuration.enabled else "paused",
            "last_error": None,
        },
    )


def create_signal_job_configuration(
    *,
    name: str,
    universe_uid: uuid.UUID | str,
    account_uid: uuid.UUID | str,
    schedule_type: str,
    schedule_every: int | None = None,
    schedule_period: str | None = None,
    schedule_expression: str | None = None,
    schedule_start_time: Any | None = None,
    description: str | None = None,
    enabled: bool = True,
    cpu_request: str | int | float = "0.25",
    memory_request: str | int | float = "0.5",
    max_runtime_seconds: int = 3600,
    spot: bool = False,
) -> AlpacaETFSignalJobConfiguration:
    """Persist desired state, materialize its Signal, and provision one Job."""
    _current_environment_uid()
    normalized_cpu, normalized_memory = _normalize_compute(
        cpu_request=cpu_request,
        memory_request=memory_request,
    )
    configuration = create_signal_job_configuration_row(
        name=name,
        universe_uid=universe_uid,
        account_uid=account_uid,
        schedule_type=schedule_type,
        schedule_every=schedule_every,
        schedule_period=schedule_period,
        schedule_expression=schedule_expression,
        schedule_start_time=schedule_start_time,
        description=description,
        enabled=enabled,
        cpu_request=normalized_cpu,
        memory_request=normalized_memory,
        max_runtime_seconds=max_runtime_seconds,
        spot=spot,
    )
    return reconcile_signal_job_configuration(configuration.uid)


def update_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
    *,
    name: str | None = None,
    description: str | None | object = _UNSET,
    universe_uid: uuid.UUID | str | None = None,
    account_uid: uuid.UUID | str | None = None,
    enabled: bool | None = None,
    schedule_type: str | None = None,
    schedule_every: int | None | object = _UNSET,
    schedule_period: str | None | object = _UNSET,
    schedule_expression: str | None | object = _UNSET,
    schedule_start_time: Any | object = _UNSET,
    cpu_request: str | int | float | None = None,
    memory_request: str | int | float | None = None,
    max_runtime_seconds: int | None = None,
    spot: bool | None = None,
) -> AlpacaETFSignalJobConfiguration:
    """Update desired state and reconcile the linked Job."""
    _current_environment_uid()
    current = get_signal_job_configuration(configuration_uid)
    if current is None:
        raise LookupError(f"Signal Job configuration {configuration_uid!s} does not exist.")
    normalized_universe_uid = uuid.UUID(str(universe_uid or current.universe_uid))
    normalized_account_uid = uuid.UUID(str(account_uid or current.account_uid))
    validate_signal_job_references(
        universe_uid=normalized_universe_uid,
        account_uid=normalized_account_uid,
    )
    if normalized_universe_uid != current.universe_uid:
        conflicts = signal_job_configurations_for_universe(normalized_universe_uid)
        if any(row.uid != current.uid for row in conflicts):
            raise ValueError(
                f"Asset Universe {normalized_universe_uid!s} already has a signal Job "
                "configuration in this Environment."
            )
    requested_schedule_type = schedule_type or current.schedule_type
    schedule = normalize_schedule(
        schedule_type=requested_schedule_type,
        schedule_every=(
            current.schedule_every if schedule_every is _UNSET else schedule_every
        ),
        schedule_period=(
            current.schedule_period if schedule_period is _UNSET else schedule_period
        ),
        schedule_expression=(
            current.schedule_expression
            if schedule_expression is _UNSET
            else schedule_expression
        ),
        schedule_start_time=(
            current.schedule_start_time
            if schedule_start_time is _UNSET
            else schedule_start_time
        ),
    )
    normalized_cpu, normalized_memory = _normalize_compute(
        cpu_request=cpu_request if cpu_request is not None else current.cpu_request,
        memory_request=(
            memory_request if memory_request is not None else current.memory_request
        ),
    )
    if max_runtime_seconds is not None and max_runtime_seconds <= 0:
        raise ValueError("max_runtime_seconds must be positive.")
    values: dict[str, Any] = {
        "universe_uid": normalized_universe_uid,
        "account_uid": normalized_account_uid,
        "enabled": current.enabled if enabled is None else bool(enabled),
        **schedule,
        "cpu_request": normalized_cpu,
        "memory_request": normalized_memory,
        "max_runtime_seconds": max_runtime_seconds or current.max_runtime_seconds,
        "spot": current.spot if spot is None else bool(spot),
        "lifecycle_state": "provisioning",
        "last_error": None,
    }
    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Signal Job configuration name must not be empty.")
        values["name"] = normalized_name
    if description is not _UNSET:
        values["description"] = (
            str(description).strip() if description is not None else None
        ) or None
    update_signal_job_configuration_row(current.uid, values=values)
    return reconcile_signal_job_configuration(current.uid)


def pause_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaETFSignalJobConfiguration:
    return update_signal_job_configuration(
        configuration_uid,
        enabled=False,
    )


def resume_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaETFSignalJobConfiguration:
    return update_signal_job_configuration(
        configuration_uid,
        enabled=True,
    )


def delete_signal_job_configuration(
    configuration_uid: uuid.UUID | str,
) -> dict[str, Any]:
    """Delete the platform Job first, then its durable configuration row."""
    _current_environment_uid()
    configuration = get_signal_job_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Signal Job configuration {configuration_uid!s} does not exist.")
    update_signal_job_configuration_row(
        configuration.uid,
        values={"lifecycle_state": "deleting", "last_error": None},
    )
    if configuration.job_uid is not None:
        try:
            job = _get_job(configuration.job_uid)
            if job is not None:
                job.delete(timeout=60)
        except Exception:
            _record_reconciliation_error(configuration.uid, operation="deletion")
            raise
    result = delete_signal_job_configuration_row(configuration.uid)
    return {
        "configuration_uid": str(configuration.uid),
        "job_uid": str(configuration.job_uid) if configuration.job_uid else None,
        "deleted": True,
        "storage_result": result,
    }


def list_signal_job_runs(
    configuration_uid: uuid.UUID | str,
    *,
    limit: int = 25,
) -> list[SignalJobRun]:
    from mainsequence.client import JobRun

    configuration = get_signal_job_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Signal Job configuration {configuration_uid!s} does not exist.")
    if configuration.job_uid is None:
        return []
    rows = list(JobRun.filter(job__uid=str(configuration.job_uid), timeout=60))
    rows.sort(
        key=lambda run: run.execution_start or dt.datetime.min.replace(tzinfo=dt.UTC),
        reverse=True,
    )
    result: list[SignalJobRun] = []
    for run in rows[:limit]:
        observability = run.observability
        status = _normalized_status(run.status)
        result.append(
            SignalJobRun(
                uid=str(run.uid),
                job_uid=str(run.job_uid),
                job_name=str(run.job_name or ""),
                status=status,
                execution_start=run.execution_start,
                execution_end=run.execution_end,
                commit_hash=run.commit_hash,
                runtime_image_uid=(str(run.runtime_image_uid) if run.runtime_image_uid else None),
                runtime_image_digest=run.runtime_image_digest,
                logs_url=(observability.application_logs_url if observability else None),
                failure_message=("The signal update failed." if status == "FAILED" else None),
            )
        )
    return result


def get_signal_job_run(job_run_uid: uuid.UUID | str) -> tuple[str, SignalJobRun] | None:
    """Return one signal JobRun and its configuration UID, if it belongs to this workflow."""
    from mainsequence.client import JobRun

    canonical_uid = _canonical_uid(job_run_uid, label="JobRun UID")
    rows = list(JobRun.filter(uid=canonical_uid, timeout=60))
    if not rows:
        return None
    if len(rows) > 1:
        raise RuntimeError(f"More than one JobRun resolved for UID {canonical_uid!r}.")
    run = rows[0]
    if not run.job_uid:
        return None
    configuration = get_signal_job_configuration_by_job_uid(run.job_uid)
    if configuration is None:
        return None
    observability = run.observability
    status = _normalized_status(run.status)
    return str(configuration.uid), SignalJobRun(
        uid=str(run.uid),
        job_uid=str(run.job_uid),
        job_name=str(run.job_name or ""),
        status=status,
        execution_start=run.execution_start,
        execution_end=run.execution_end,
        commit_hash=run.commit_hash,
        runtime_image_uid=(str(run.runtime_image_uid) if run.runtime_image_uid else None),
        runtime_image_digest=run.runtime_image_digest,
        logs_url=(observability.application_logs_url if observability else None),
        failure_message=("The signal update failed." if status == "FAILED" else None),
    )


def launch_signal_job(
    configuration_uid: uuid.UUID | str,
) -> SignalJobSubmission:
    """Launch the linked Job with no per-run business arguments."""
    _current_environment_uid()
    configuration = get_signal_job_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Signal Job configuration {configuration_uid!s} does not exist.")
    if not configuration.enabled:
        raise ValueError(f"Signal Job configuration {configuration.uid!s} is paused.")
    validate_signal_job_references(
        universe_uid=configuration.universe_uid,
        account_uid=configuration.account_uid,
    )
    job = _require_linked_job(configuration)
    if not _job_image_is_ready(job):
        raise ValueError(f"Signal Job {job.uid!s} image is not ready.")
    recent_runs = list_signal_job_runs(configuration.uid, limit=1)
    if recent_runs and recent_runs[0].status in RUNNING_JOB_STATUSES:
        raise ValueError(f"Signal Job {job.uid!s} already has a pending or running execution.")
    payload = job.run_job(timeout=60)
    job_run_uid = payload.get("uid") or payload.get("job_run_uid")
    if not job_run_uid:
        raise RuntimeError("Main Sequence accepted the Job request without a JobRun UID.")
    return SignalJobSubmission(
        configuration_uid=str(configuration.uid),
        job_uid=str(job.uid),
        job_run_uid=str(job_run_uid),
        status=_normalized_status(payload.get("status")),
    )


def resolve_current_signal_job_configuration(
    *,
    job_run_uid: uuid.UUID | str | None = None,
) -> tuple[AlpacaETFSignalJobConfiguration, Any]:
    """Resolve runtime configuration strictly from the backend-owned JobRun identity."""
    from mainsequence.client import JobRun

    raw_job_run_uid = str(job_run_uid or os.getenv("JOB_RUN_UID") or "").strip()
    if not raw_job_run_uid:
        raise RuntimeError("JOB_RUN_UID is required for the signal Job launcher.")
    canonical_job_run_uid = _canonical_uid(raw_job_run_uid, label="JOB_RUN_UID")
    rows = list(JobRun.filter(uid=canonical_job_run_uid, timeout=60))
    if len(rows) != 1:
        raise RuntimeError(
            f"JOB_RUN_UID {canonical_job_run_uid!r} did not resolve exactly one JobRun."
        )
    run = rows[0]
    if not run.job_uid:
        raise RuntimeError(f"JobRun {canonical_job_run_uid!r} has no owning Job UID.")
    configuration = get_signal_job_configuration_by_job_uid(run.job_uid)
    if configuration is None:
        raise RuntimeError(
            f"Job {run.job_uid!s} has no Alpaca ETF signal Job configuration."
        )
    if not configuration.enabled:
        raise RuntimeError(f"Signal Job configuration {configuration.uid!s} is paused.")
    if run.organization_environment_uid:
        current_environment_uid = _current_environment_uid()
        if str(run.organization_environment_uid) != current_environment_uid:
            raise RuntimeError(
                "The JobRun Environment does not match the runtime CodeRepositoryBranch "
                "Environment."
            )
    return configuration, run


def execute_current_signal_job(
    *,
    job_run_uid: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    """Run one signal update after resolving configuration from the owning Job."""
    from src.universes import run_asset_universe

    configuration, run = resolve_current_signal_job_configuration(job_run_uid=job_run_uid)
    result = run_asset_universe(
        configuration.universe_uid,
        account_uid=configuration.account_uid,
    )
    return {
        "configuration_uid": str(configuration.uid),
        "job_uid": str(run.job_uid),
        "job_run_uid": str(run.uid),
        "universe_uid": str(configuration.universe_uid),
        "signal_uid": result.signal_uid,
        "observed_at": result.observed_at,
        "signal_weight_row_count": result.signal_weight_row_count,
        "asset_count": len(result.asset_uids),
    }


__all__ = [
    "ALPACA_ETF_SIGNAL_EXECUTION_PATH",
    "SignalJobRun",
    "SignalJobSubmission",
    "create_signal_job_configuration",
    "delete_signal_job_configuration",
    "execute_current_signal_job",
    "get_signal_job_run",
    "launch_signal_job",
    "list_signal_job_runs",
    "pause_signal_job_configuration",
    "reconcile_signal_job_configuration",
    "resolve_current_signal_job_configuration",
    "resume_signal_job_configuration",
    "signal_uid_for_configuration",
    "update_signal_job_configuration",
]
