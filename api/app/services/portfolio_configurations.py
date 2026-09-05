"""API shaping for durable analytical ETF portfolio configurations."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Any, Mapping

from src.operations import (
    create_portfolio_job_configuration,
    delete_portfolio_job_configuration,
    launch_portfolio_job,
    list_portfolio_job_runs,
    normalize_portfolio_job_settings,
    signal_job_configurations_by_uids,
    signal_uid_for_configuration,
    update_portfolio_job_configuration,
)
from src.portfolios import (
    PortfolioRebalanceConfiguration,
    create_rebalance_configuration,
    delete_rebalance_configuration,
    get_portfolio_configuration,
    get_rebalance_configuration,
    list_portfolio_configurations,
    list_rebalance_configurations,
    rebalance_configurations_by_uids,
    update_rebalance_configuration,
)

from ..schemas import (
    PortfolioConfigurationCreateRequest,
    PortfolioConfigurationResponse,
    PortfolioConfigurationUpdateRequest,
    PortfolioJobResponse,
    PortfolioJobRunAcceptedResponse,
    PortfolioJobRunResponse,
    PortfolioRebalanceConfigurationCreateRequest,
    PortfolioRebalanceConfigurationResponse,
    PortfolioRebalanceConfigurationUpdateRequest,
)
from .common import collection_response


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _schedule_payload(job: Any) -> dict[str, Any]:
    task_schedule = getattr(job, "task_schedule", None)
    schedule = _value(task_schedule, "schedule") if task_schedule is not None else None
    if schedule is None:
        return {
            "schedule_type": None,
            "schedule_every": None,
            "schedule_period": None,
            "schedule_expression": None,
            "schedule_start_time": None,
        }
    schedule_type = str(_value(schedule, "type", _value(schedule, "schedule_type", ""))).lower()
    if schedule_type == "interval":
        return {
            "schedule_type": "interval",
            "schedule_every": _value(schedule, "every"),
            "schedule_period": _value(schedule, "period"),
            "schedule_expression": None,
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
        "schedule_start_time": _value(schedule, "start_time"),
    }


def _job_response(job: Any | None) -> PortfolioJobResponse | None:
    if job is None:
        return None
    image_status = getattr(job, "image_status", None)
    image_status = getattr(image_status, "value", image_status)
    return PortfolioJobResponse(
        uid=str(job.uid),
        **_schedule_payload(job),
        cpu_request=(str(job.cpu_request) if job.cpu_request is not None else None),
        memory_request=(str(job.memory_request) if job.memory_request is not None else None),
        max_runtime_seconds=job.max_runtime_seconds,
        spot=bool(job.spot),
        image_status=(str(image_status) if image_status is not None else None),
        automatic_deployment=bool(job.automatic_deployment),
    )


def _runtime_by_job_uid(rows: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from mainsequence.client import Job, JobRun

    job_uids = list(dict.fromkeys(str(row.job_uid) for row in rows if row.job_uid is not None))
    if not job_uids:
        return {}, {}
    jobs = list(Job.filter(uid__in=job_uids, timeout=60))
    runs = list(JobRun.filter(job__uid__in=job_uids, timeout=60))
    jobs_by_uid = {str(job.uid): job for job in jobs}
    latest_by_job_uid: dict[str, Any] = {}
    minimum = dt.datetime.min.replace(tzinfo=dt.UTC)
    for run in runs:
        job_uid = str(run.job_uid or "")
        current = latest_by_job_uid.get(job_uid)
        if current is None or (run.execution_start or minimum) > (
            current.execution_start or minimum
        ):
            latest_by_job_uid[job_uid] = run
    return jobs_by_uid, latest_by_job_uid


def _canonical_status(value: Any) -> str:
    raw = getattr(value, "value", value)
    status = str(raw or "").strip().upper()
    return {
        "COMPLETE": "SUCCEEDED",
        "COMPLETED": "SUCCEEDED",
        "SUCCESS": "SUCCEEDED",
        "SUCCESSFUL": "SUCCEEDED",
        "ERROR": "FAILED",
        "ERRORED": "FAILED",
    }.get(status, status)


def _responses(rows: list[Any]) -> list[PortfolioConfigurationResponse]:
    if not rows:
        return []
    jobs, latest_runs = _runtime_by_job_uid(rows)
    signal_uids = list(dict.fromkeys(str(row.signal_configuration_uid) for row in rows))
    rebalance_uids = list(dict.fromkeys(str(row.rebalance_configuration_uid) for row in rows))
    signal_configurations = signal_job_configurations_by_uids(signal_uids)
    rebalance_configurations = rebalance_configurations_by_uids(rebalance_uids)
    responses: list[PortfolioConfigurationResponse] = []
    for row in rows:
        signal_configuration = signal_configurations.get(str(row.signal_configuration_uid))
        rebalance_configuration = rebalance_configurations.get(str(row.rebalance_configuration_uid))
        if signal_configuration is None or rebalance_configuration is None:
            raise RuntimeError(
                f"Portfolio configuration {row.uid!s} has an unresolved required relationship."
            )
        latest_run = latest_runs.get(str(row.job_uid))
        responses.append(
            PortfolioConfigurationResponse.model_validate(
                {
                    **row.model_dump(mode="json"),
                    "signal_uid": signal_uid_for_configuration(signal_configuration),
                    "rebalance_strategy": rebalance_configuration.strategy,
                    "job": _job_response(jobs.get(str(row.job_uid))),
                    "latest_run_status": (
                        _canonical_status(latest_run.status) if latest_run is not None else None
                    ),
                    "latest_run_at": (
                        latest_run.execution_start if latest_run is not None else None
                    ),
                }
            )
        )
    return responses


def list_configurations(
    *,
    limit: int,
    offset: int,
    search: str | None,
    signal_configuration_uid: str | None,
    bars_configuration_uid: str | None,
    ordering: str,
):
    rows, total = list_portfolio_configurations(
        limit=limit,
        offset=offset,
        search=search,
        signal_configuration_uid=signal_configuration_uid,
        bars_configuration_uid=bars_configuration_uid,
        ordering=ordering,
    )
    return collection_response(
        items=_responses(rows),
        total=total,
        limit=limit,
        offset=offset,
    )


def get_configuration(configuration_uid: str) -> PortfolioConfigurationResponse | None:
    row = get_portfolio_configuration(configuration_uid)
    return _responses([row])[0] if row is not None else None


def create_configuration(
    request: PortfolioConfigurationCreateRequest,
) -> PortfolioConfigurationResponse:
    payload = request.model_dump()
    job = payload.pop("job")
    row = create_portfolio_job_configuration(**payload, **job)
    return _responses([row])[0]


def update_configuration(
    configuration_uid: str,
    request: PortfolioConfigurationUpdateRequest,
) -> PortfolioConfigurationResponse:
    payload = request.model_dump(exclude_unset=True)
    job_payload = payload.pop("job", None)
    settings = normalize_portfolio_job_settings(**job_payload) if job_payload is not None else None
    row = update_portfolio_job_configuration(
        configuration_uid,
        job_settings=settings,
        **payload,
    )
    return _responses([row])[0]


def delete_configuration(configuration_uid: str) -> dict[str, Any]:
    return delete_portfolio_job_configuration(configuration_uid)


def run_configuration(configuration_uid: str) -> PortfolioJobRunAcceptedResponse:
    submission = launch_portfolio_job(configuration_uid)
    return PortfolioJobRunAcceptedResponse(
        configuration_uid=submission.configuration_uid,
        job_uid=submission.job_uid,
        job_run_uid=submission.job_run_uid,
        status=submission.status,
        status_url=f"/v1/operations/job-runs/{submission.job_run_uid}",
    )


def configuration_runs(configuration_uid: str, *, limit: int) -> list[PortfolioJobRunResponse]:
    return [
        PortfolioJobRunResponse.model_validate(asdict(run))
        for run in list_portfolio_job_runs(configuration_uid, limit=limit)
    ]


def list_rebalances(*, limit: int, offset: int, search: str | None, ordering: str):
    rows, total = list_rebalance_configurations(
        limit=limit,
        offset=offset,
        search=search,
        ordering=ordering,
    )
    return collection_response(
        items=[_rebalance_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _rebalance_response(
    row: PortfolioRebalanceConfiguration,
) -> PortfolioRebalanceConfigurationResponse:
    """Convert the storage-domain model into the API response contract."""

    return PortfolioRebalanceConfigurationResponse.model_validate(row.model_dump(mode="json"))


def create_rebalance(
    request: PortfolioRebalanceConfigurationCreateRequest,
) -> PortfolioRebalanceConfigurationResponse:
    return _rebalance_response(create_rebalance_configuration(**request.model_dump()))


def get_rebalance(configuration_uid: str) -> PortfolioRebalanceConfigurationResponse | None:
    row = get_rebalance_configuration(configuration_uid)
    return _rebalance_response(row) if row else None


def update_rebalance(
    configuration_uid: str,
    request: PortfolioRebalanceConfigurationUpdateRequest,
) -> PortfolioRebalanceConfigurationResponse:
    return _rebalance_response(
        update_rebalance_configuration(
            configuration_uid,
            **request.model_dump(exclude_unset=True),
        )
    )


def delete_rebalance(configuration_uid: str) -> dict[str, Any]:
    return delete_rebalance_configuration(configuration_uid)


__all__ = [
    "configuration_runs",
    "create_configuration",
    "create_rebalance",
    "delete_configuration",
    "delete_rebalance",
    "get_configuration",
    "get_rebalance",
    "list_configurations",
    "list_rebalances",
    "run_configuration",
    "update_configuration",
    "update_rebalance",
]
