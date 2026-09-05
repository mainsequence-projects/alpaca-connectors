"""API shaping for Universe-backed Alpaca ETF signal Jobs."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Any

from src.operations import (
    create_signal_job_configuration,
    delete_signal_job_configuration,
    get_signal_job_configuration,
    launch_signal_job,
    list_signal_job_configurations,
    list_signal_job_runs,
    pause_signal_job_configuration,
    reconcile_signal_job_configuration,
    resume_signal_job_configuration,
    signal_uid_for_configuration,
    update_signal_job_configuration,
)

from ..schemas import (
    SignalJobConfigurationCreateRequest,
    SignalJobConfigurationResponse,
    SignalJobConfigurationUpdateRequest,
    SignalJobRunAcceptedResponse,
    SignalJobRunResponse,
    SignalObservationAssetResponse,
    SignalObservationsResponse,
)
from .common import collection_response


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


def _runtime_by_job_uid(rows: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load all related Jobs and latest JobRuns with two set-based platform requests."""
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


def _response(
    row: Any,
    *,
    job: Any | None,
    latest_run: Any | None,
) -> SignalJobConfigurationResponse:
    payload = row.model_dump(mode="json")
    payload.update(
        {
            "signal_uid": signal_uid_for_configuration(row),
            "job_image_status": (
                str(getattr(job.image_status, "value", job.image_status)) if job else None
            ),
            "job_automatic_deployment": (
                bool(job.automatic_deployment) if job is not None else None
            ),
            "latest_run_status": (
                _canonical_status(latest_run.status) if latest_run is not None else None
            ),
            "latest_run_at": (
                latest_run.execution_start if latest_run is not None else None
            ),
        }
    )
    return SignalJobConfigurationResponse.model_validate(payload)


def _responses(rows: list[Any]) -> list[SignalJobConfigurationResponse]:
    jobs, latest_runs = _runtime_by_job_uid(rows)
    return [
        _response(
            row,
            job=jobs.get(str(row.job_uid)),
            latest_run=latest_runs.get(str(row.job_uid)),
        )
        for row in rows
    ]


def list_configurations(
    *,
    limit: int,
    offset: int,
    search: str | None,
    enabled: bool | None,
    lifecycle_state: str | None,
    ordering: str,
):
    rows, total = list_signal_job_configurations(
        limit=limit,
        offset=offset,
        search=search,
        enabled=enabled,
        lifecycle_state=lifecycle_state,
        ordering=ordering,
    )
    return collection_response(
        items=_responses(rows),
        total=total,
        limit=limit,
        offset=offset,
    )


def get_configuration(configuration_uid: str) -> SignalJobConfigurationResponse | None:
    row = get_signal_job_configuration(configuration_uid)
    return _responses([row])[0] if row else None


def get_configuration_observations(
    configuration_uid: str,
    *,
    limit: int,
) -> SignalObservationsResponse:
    """Resolve configuration identity and read its latest complete signal observations."""
    from src.portfolios import read_signal_observation_matrix

    row = get_signal_job_configuration(configuration_uid)
    if row is None:
        raise LookupError(f"Signal Job configuration {configuration_uid!s} does not exist.")
    matrix = read_signal_observation_matrix(
        signal_uid=signal_uid_for_configuration(row),
        observation_limit=limit,
    )
    assets = [
        SignalObservationAssetResponse(
            asset_identifier=asset.asset_identifier,
            symbol=asset.symbol,
            name=asset.name,
            weights=list(asset.weights),
        )
        for asset in matrix.assets
    ]
    return SignalObservationsResponse(
        configuration_uid=str(row.uid),
        signal_uid=matrix.signal_uid,
        observation_count=len(matrix.time_indexes),
        asset_count=len(assets),
        time_indexes=list(matrix.time_indexes),
        assets=assets,
    )


def create_configuration(
    request: SignalJobConfigurationCreateRequest,
) -> SignalJobConfigurationResponse:
    row = create_signal_job_configuration(**request.model_dump())
    return _responses([row])[0]


def update_configuration(
    configuration_uid: str,
    request: SignalJobConfigurationUpdateRequest,
) -> SignalJobConfigurationResponse:
    row = update_signal_job_configuration(
        configuration_uid,
        **request.model_dump(exclude_unset=True),
    )
    return _responses([row])[0]


def delete_configuration(configuration_uid: str) -> dict[str, Any]:
    return delete_signal_job_configuration(configuration_uid)


def pause_configuration(
    configuration_uid: str,
) -> SignalJobConfigurationResponse:
    row = pause_signal_job_configuration(configuration_uid)
    return _responses([row])[0]


def resume_configuration(
    configuration_uid: str,
) -> SignalJobConfigurationResponse:
    row = resume_signal_job_configuration(configuration_uid)
    return _responses([row])[0]


def reconcile_configuration(
    configuration_uid: str,
) -> SignalJobConfigurationResponse:
    row = reconcile_signal_job_configuration(configuration_uid)
    return _responses([row])[0]


def run_configuration(
    configuration_uid: str,
) -> SignalJobRunAcceptedResponse:
    submission = launch_signal_job(configuration_uid)
    return SignalJobRunAcceptedResponse(
        configuration_uid=submission.configuration_uid,
        job_uid=submission.job_uid,
        job_run_uid=submission.job_run_uid,
        status=submission.status,
        status_url=f"/v1/operations/job-runs/{submission.job_run_uid}",
    )


def configuration_runs(configuration_uid: str, *, limit: int) -> list[SignalJobRunResponse]:
    return [
        SignalJobRunResponse.model_validate(asdict(run))
        for run in list_signal_job_runs(configuration_uid, limit=limit)
    ]


__all__ = [
    "configuration_runs",
    "create_configuration",
    "delete_configuration",
    "get_configuration",
    "get_configuration_observations",
    "list_configurations",
    "pause_configuration",
    "reconcile_configuration",
    "resume_configuration",
    "run_configuration",
    "update_configuration",
]
