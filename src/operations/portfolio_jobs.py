"""Dedicated Main Sequence Job lifecycle for durable ETF portfolio configurations."""

from __future__ import annotations

import datetime as dt
import os
import uuid
from dataclasses import dataclass
from typing import Any

from src.operations.signal_job_configurations import normalize_schedule
from src.portfolios.configurations import (
    AlpacaETFPortfolioConfiguration,
    create_portfolio_configuration_row,
    delete_portfolio_configuration_row,
    get_portfolio_configuration,
    get_portfolio_configuration_by_job_uid,
    update_portfolio_calculation_configuration,
    update_portfolio_configuration_row,
)

ALPACA_ETF_PORTFOLIO_EXECUTION_PATH = "src/jobs/run_alpaca_etf_portfolio.py"
RUNNING_JOB_STATUSES = frozenset({"PENDING", "QUEUED", "RUNNING", "STARTED"})


@dataclass(frozen=True, slots=True)
class PortfolioJobSettings:
    schedule_type: str
    schedule_every: int | None
    schedule_period: str | None
    schedule_expression: str | None
    schedule_timezone: str | None
    schedule_start_time: dt.datetime | None
    cpu_request: str
    memory_request: str
    max_runtime_seconds: int
    spot: bool


@dataclass(frozen=True, slots=True)
class PortfolioJobSubmission:
    configuration_uid: str
    job_uid: str
    job_run_uid: str
    status: str


@dataclass(frozen=True, slots=True)
class PortfolioJobRun:
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


def _current_environment_uid() -> str:
    from mainsequence.code_repository_context import resolve_organization_environment_uid

    return resolve_organization_environment_uid("Alpaca ETF portfolio Job operation")


def _normalized_status(value: Any) -> str:
    raw = getattr(value, "value", value)
    status = str(raw or "queued").strip().upper()
    return {
        "COMPLETE": "SUCCEEDED",
        "COMPLETED": "SUCCEEDED",
        "SUCCESS": "SUCCEEDED",
        "SUCCESSFUL": "SUCCEEDED",
        "ERROR": "FAILED",
        "ERRORED": "FAILED",
    }.get(status, status)


def _job_image_is_ready(job: Any) -> bool:
    value = getattr(job, "image_status", None)
    value = getattr(value, "value", value)
    return str(value or "").strip().lower() == "ready"


def normalize_portfolio_job_settings(
    *,
    schedule_type: str,
    schedule_every: int | None = None,
    schedule_period: str | None = None,
    schedule_expression: str | None = None,
    schedule_timezone: str | None = None,
    schedule_start_time: dt.datetime | None = None,
    cpu_request: str | int | float = "0.25",
    memory_request: str | int | float = "0.5",
    max_runtime_seconds: int = 3600,
    spot: bool = False,
) -> PortfolioJobSettings:
    from mainsequence.client.compute_validation import validate_and_normalize_compute_fields

    schedule = normalize_schedule(
        schedule_type=schedule_type,
        schedule_every=schedule_every,
        schedule_period=schedule_period,
        schedule_expression=schedule_expression,
        schedule_timezone=schedule_timezone,
        schedule_start_time=schedule_start_time,
    )
    compute = validate_and_normalize_compute_fields(
        cpu_request=cpu_request,
        memory_request=memory_request,
        gpu_request=None,
        gpu_type=None,
        require_cpu_and_memory=True,
        output_format="decimal",
    )
    if max_runtime_seconds <= 0:
        raise ValueError("max_runtime_seconds must be positive.")
    return PortfolioJobSettings(
        **schedule,
        cpu_request=str(compute["cpu_request"]),
        memory_request=str(compute["memory_request"]),
        max_runtime_seconds=int(max_runtime_seconds),
        spot=bool(spot),
    )


def _job_name(configuration: AlpacaETFPortfolioConfiguration) -> str:
    suffix = str(configuration.uid).split("-", 1)[0]
    uid_suffix = f" [{suffix}]"
    prefix = f"Alpaca ETF Portfolio — {configuration.name}"
    return prefix[: 220 - len(uid_suffix)] + uid_suffix


def _job_description(configuration: AlpacaETFPortfolioConfiguration) -> str:
    detail = configuration.description or "Scheduled analytical ETF portfolio update."
    return (
        f"{detail} Portfolio configuration {configuration.uid!s}; signal configuration "
        f"{configuration.signal_configuration_uid!s}; bars configuration "
        f"{configuration.bars_configuration_uid!s}."
    )


def _job_schedule_update(settings: PortfolioJobSettings) -> dict[str, Any]:
    if settings.schedule_type == "interval":
        schedule: dict[str, Any] = {
            "schedule_type": "interval",
            "every": settings.schedule_every,
            "period": settings.schedule_period,
        }
    else:
        minute, hour, day_of_month, month_of_year, day_of_week = str(
            settings.schedule_expression
        ).split()
        schedule = {
            "schedule_type": "crontab",
            "minute": minute,
            "hour": hour,
            "day_of_month": day_of_month,
            "month_of_year": month_of_year,
            "day_of_week": day_of_week,
        }
        if settings.schedule_timezone is not None:
            schedule["timezone"] = settings.schedule_timezone
    if settings.schedule_start_time is not None:
        schedule["start_time"] = settings.schedule_start_time.isoformat()
    schedule["one_off"] = False
    return schedule


def _get_job(job_uid: uuid.UUID | str) -> Any | None:
    from src.operations.platform_jobs import PlatformJob

    rows = list(PlatformJob.filter(uid=_canonical_uid(job_uid, label="Job UID"), timeout=60))
    if len(rows) > 1:
        raise RuntimeError(f"More than one Job resolved for UID {job_uid!s}.")
    return rows[0] if rows else None


def _require_linked_job(configuration: AlpacaETFPortfolioConfiguration) -> Any:
    if configuration.job_uid is None:
        raise ValueError(
            f"Portfolio configuration {configuration.uid!s} has not provisioned its Job."
        )
    job = _get_job(configuration.job_uid)
    if job is None:
        raise ValueError(
            f"Main Sequence Job {configuration.job_uid!s} linked to portfolio configuration "
            f"{configuration.uid!s} no longer exists."
        )
    if job.execution_path != ALPACA_ETF_PORTFOLIO_EXECUTION_PATH:
        raise RuntimeError(
            f"Job {job.uid!s} linked to portfolio configuration {configuration.uid!s} does "
            "not use the reviewed portfolio launcher."
        )
    return job


def _create_platform_job(
    configuration: AlpacaETFPortfolioConfiguration,
    settings: PortfolioJobSettings,
) -> Any:
    from src.operations.platform_jobs import PlatformJob

    return PlatformJob.create(
        name=_job_name(configuration),
        description=_job_description(configuration),
        execution_path=ALPACA_ETF_PORTFOLIO_EXECUTION_PATH,
        task_schedule=None,
        cpu_request=settings.cpu_request,
        memory_request=settings.memory_request,
        spot=settings.spot,
        max_runtime_seconds=settings.max_runtime_seconds,
        automatic_deployment=True,
        timeout=60,
    )


def _patch_platform_job(
    configuration: AlpacaETFPortfolioConfiguration,
    job: Any,
    settings: PortfolioJobSettings,
) -> Any:
    patched = job.patch(
        name=_job_name(configuration),
        description=_job_description(configuration),
        cpu_request=settings.cpu_request,
        memory_request=settings.memory_request,
        spot=settings.spot,
        max_runtime_seconds=settings.max_runtime_seconds,
        automatic_deployment=True,
        create_schedule=True,
        schedule=_job_schedule_update(settings),
    )
    if getattr(patched, "task_schedule", None) is None:
        raise RuntimeError(f"Main Sequence Job {patched.uid!s} did not persist its schedule.")
    return patched


def provision_portfolio_job(
    configuration_uid: uuid.UUID | str,
    *,
    settings: PortfolioJobSettings,
) -> AlpacaETFPortfolioConfiguration:
    """Create or update the one Job linked to the portfolio configuration."""
    _current_environment_uid()
    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    job = _get_job(configuration.job_uid) if configuration.job_uid is not None else None
    if job is None:
        job = _create_platform_job(configuration, settings)
        if not getattr(job, "uid", None):
            raise RuntimeError("Main Sequence created a portfolio Job without returning its UID.")
        configuration = update_portfolio_configuration_row(
            configuration.uid,
            values={"job_uid": uuid.UUID(str(job.uid))},
        )
    _patch_platform_job(configuration, job, settings)
    return configuration


def create_portfolio_job_configuration(
    *,
    name: str,
    signal_configuration_uid: uuid.UUID | str,
    bars_configuration_uid: uuid.UUID | str,
    rebalance_configuration_uid: uuid.UUID | str,
    schedule_type: str,
    schedule_every: int | None = None,
    schedule_period: str | None = None,
    schedule_expression: str | None = None,
    schedule_timezone: str | None = None,
    schedule_start_time: dt.datetime | None = None,
    description: str | None = None,
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
    valuation_column: str = "close",
    portfolio_prices_frequency: str | None = "1d",
    forward_fill_to_now: bool = False,
    fail_on_missing_prices: bool = True,
    commission_fee: float = 0.00018,
    cpu_request: str | int | float = "0.25",
    memory_request: str | int | float = "0.5",
    max_runtime_seconds: int = 3600,
    spot: bool = False,
) -> AlpacaETFPortfolioConfiguration:
    settings = normalize_portfolio_job_settings(
        schedule_type=schedule_type,
        schedule_every=schedule_every,
        schedule_period=schedule_period,
        schedule_expression=schedule_expression,
        schedule_timezone=schedule_timezone,
        schedule_start_time=schedule_start_time,
        cpu_request=cpu_request,
        memory_request=memory_request,
        max_runtime_seconds=max_runtime_seconds,
        spot=spot,
    )
    _current_environment_uid()
    configuration = create_portfolio_configuration_row(
        name=name,
        description=description,
        signal_configuration_uid=signal_configuration_uid,
        bars_configuration_uid=bars_configuration_uid,
        rebalance_configuration_uid=rebalance_configuration_uid,
        upsample_frequency_id=upsample_frequency_id,
        intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
        valuation_column=valuation_column,
        portfolio_prices_frequency=portfolio_prices_frequency,
        forward_fill_to_now=forward_fill_to_now,
        fail_on_missing_prices=fail_on_missing_prices,
        commission_fee=commission_fee,
    )
    return provision_portfolio_job(configuration.uid, settings=settings)


def update_portfolio_job_configuration(
    configuration_uid: uuid.UUID | str,
    *,
    job_settings: PortfolioJobSettings | None = None,
    **calculation_values: Any,
) -> AlpacaETFPortfolioConfiguration:
    _current_environment_uid()
    if calculation_values:
        configuration = update_portfolio_calculation_configuration(
            configuration_uid,
            **calculation_values,
        )
    else:
        configuration = get_portfolio_configuration(configuration_uid)
        if configuration is None:
            raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    if job_settings is not None:
        return provision_portfolio_job(configuration.uid, settings=job_settings)
    if configuration.job_uid is not None:
        job = _require_linked_job(configuration)
        job.patch(
            name=_job_name(configuration),
            description=_job_description(configuration),
            automatic_deployment=True,
        )
    return configuration


def delete_portfolio_job_configuration(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    _current_environment_uid()
    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    if configuration.job_uid is not None:
        job = _get_job(configuration.job_uid)
        if job is not None:
            job.delete(timeout=60)
    result = delete_portfolio_configuration_row(configuration.uid)
    return {
        "configuration_uid": str(configuration.uid),
        "job_uid": str(configuration.job_uid) if configuration.job_uid else None,
        "deleted": True,
        "storage_result": result,
    }


def list_portfolio_job_runs(
    configuration_uid: uuid.UUID | str,
    *,
    limit: int = 25,
) -> list[PortfolioJobRun]:
    from mainsequence.client import JobRun

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    if configuration.job_uid is None:
        return []
    rows = list(JobRun.filter(job__uid=str(configuration.job_uid), timeout=60))
    rows.sort(
        key=lambda run: run.execution_start or dt.datetime.min.replace(tzinfo=dt.UTC),
        reverse=True,
    )
    result: list[PortfolioJobRun] = []
    for run in rows[:limit]:
        observability = run.observability
        status = _normalized_status(run.status)
        result.append(
            PortfolioJobRun(
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
                failure_message=("The portfolio update failed." if status == "FAILED" else None),
            )
        )
    return result


def get_portfolio_job_run(
    job_run_uid: uuid.UUID | str,
) -> tuple[str, PortfolioJobRun] | None:
    """Return one portfolio JobRun and its configuration UID when it belongs here."""
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
    configuration = get_portfolio_configuration_by_job_uid(run.job_uid)
    if configuration is None:
        return None
    observability = run.observability
    status = _normalized_status(run.status)
    return str(configuration.uid), PortfolioJobRun(
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
        failure_message=("The portfolio update failed." if status == "FAILED" else None),
    )


def launch_portfolio_job(configuration_uid: uuid.UUID | str) -> PortfolioJobSubmission:
    _current_environment_uid()
    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    job = _require_linked_job(configuration)
    if not _job_image_is_ready(job):
        raise ValueError(f"Portfolio Job {job.uid!s} image is not ready.")
    recent_runs = list_portfolio_job_runs(configuration.uid, limit=1)
    if recent_runs and recent_runs[0].status in RUNNING_JOB_STATUSES:
        raise ValueError(f"Portfolio Job {job.uid!s} already has a pending or running execution.")
    payload = job.run_job(timeout=60)
    job_run_uid = payload.get("uid") or payload.get("job_run_uid")
    if not job_run_uid:
        raise RuntimeError("Main Sequence accepted the portfolio Job without a JobRun UID.")
    return PortfolioJobSubmission(
        configuration_uid=str(configuration.uid),
        job_uid=str(job.uid),
        job_run_uid=str(job_run_uid),
        status=_normalized_status(payload.get("status")),
    )


def resolve_current_portfolio_job_configuration(
    *,
    job_run_uid: uuid.UUID | str | None = None,
) -> tuple[AlpacaETFPortfolioConfiguration, Any]:
    from mainsequence.client import JobRun

    raw_uid = str(job_run_uid or os.getenv("JOB_RUN_UID") or "").strip()
    if not raw_uid:
        raise RuntimeError("JOB_RUN_UID is required for the portfolio Job launcher.")
    canonical_uid = _canonical_uid(raw_uid, label="JOB_RUN_UID")
    rows = list(JobRun.filter(uid=canonical_uid, timeout=60))
    if len(rows) != 1:
        raise RuntimeError(f"JOB_RUN_UID {canonical_uid!r} did not resolve exactly one JobRun.")
    run = rows[0]
    if not run.job_uid:
        raise RuntimeError(f"JobRun {canonical_uid!r} has no owning Job UID.")
    configuration = get_portfolio_configuration_by_job_uid(run.job_uid)
    if configuration is None:
        raise RuntimeError(f"Job {run.job_uid!s} has no ETF portfolio configuration.")
    if run.organization_environment_uid:
        current_environment_uid = _current_environment_uid()
        if str(run.organization_environment_uid) != current_environment_uid:
            raise RuntimeError(
                "The JobRun Environment does not match the runtime CodeRepositoryBranch "
                "Environment."
            )
    return configuration, run


def execute_current_portfolio_job(
    *,
    job_run_uid: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    from src.portfolios import execute_portfolio_configuration

    configuration, run = resolve_current_portfolio_job_configuration(job_run_uid=job_run_uid)
    result = execute_portfolio_configuration(configuration.uid)
    return {**result.summary(), "job_run_uid": str(run.uid)}


__all__ = [
    "ALPACA_ETF_PORTFOLIO_EXECUTION_PATH",
    "PortfolioJobRun",
    "PortfolioJobSettings",
    "PortfolioJobSubmission",
    "create_portfolio_job_configuration",
    "delete_portfolio_job_configuration",
    "execute_current_portfolio_job",
    "get_portfolio_job_run",
    "launch_portfolio_job",
    "list_portfolio_job_runs",
    "normalize_portfolio_job_settings",
    "provision_portfolio_job",
    "resolve_current_portfolio_job_configuration",
    "update_portfolio_job_configuration",
]
