"""API shaping for Universe-backed Alpaca ETF signal configurations.

Every configuration runs on the signal Job declared in the repository workflow (ADR 0013).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from alpaca_connectors.operations import (
    configuration_job_state,
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
from .configuration_jobs import accepted_run, job_fields, lifecycle_state


def _response(
    row: Any, *, job: Any | None, latest_run: Any | None
) -> SignalJobConfigurationResponse:
    return SignalJobConfigurationResponse.model_validate(
        {
            "uid": str(row.uid),
            "name": row.name,
            "description": row.description,
            "signal_uid": signal_uid_for_configuration(row),
            "universe_uid": str(row.universe_uid),
            "account_uid": str(row.account_uid),
            "scheduled": row.scheduled,
            **job_fields(job, latest_run),
            "lifecycle_state": lifecycle_state(job, scheduled=row.scheduled),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def _responses(rows: list[Any]) -> list[SignalJobConfigurationResponse]:
    """Shape rows with one Job lookup and one bounded scan of the Job's newest runs."""
    if not rows:
        return []
    state = configuration_job_state("signal", [row.uid for row in rows])
    return [
        _response(row, job=state.job, latest_run=state.latest_runs.get(str(row.uid)))
        for row in rows
    ]


def list_configurations(
    *,
    limit: int,
    offset: int,
    search: str | None,
    scheduled: bool | None,
    ordering: str,
):
    rows, total = list_signal_job_configurations(
        limit=limit,
        offset=offset,
        search=search,
        scheduled=scheduled,
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
    from alpaca_connectors.portfolios import read_signal_observation_matrix

    row = get_signal_job_configuration(configuration_uid)
    if row is None:
        raise LookupError(f"Signal configuration {configuration_uid!s} does not exist.")
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
    return SignalJobRunAcceptedResponse.model_validate(
        accepted_run(launch_signal_job(configuration_uid))
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
