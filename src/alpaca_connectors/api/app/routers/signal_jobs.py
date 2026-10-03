"""Universe-backed Alpaca ETF signal Job API."""

from __future__ import annotations

from fastapi import APIRouter, Body, Query

from ..errors import api_http_error, not_found
from ..schemas import (
    ResourceCollection,
    ResourceDiscoveryResponse,
    SignalJobConfigurationCreateRequest,
    SignalJobConfigurationResponse,
    SignalJobConfigurationUpdateRequest,
    SignalJobRunAcceptedResponse,
    SignalJobRunResponse,
    SignalObservationsResponse,
)
from ..services.common import resource_discovery, validate_ordering, validate_page_window
from ..services.signal_jobs import (
    configuration_runs,
    create_configuration,
    delete_configuration,
    get_configuration,
    get_configuration_observations,
    list_configurations,
    pause_configuration,
    reconcile_configuration,
    resume_configuration,
    run_configuration,
    update_configuration,
)

router = APIRouter(prefix="/v1/signal-jobs", tags=["Portfolios"])


@router.get("", response_model=ResourceCollection)
def signal_jobs_list(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    enabled: bool | None = None,
    lifecycle_state: str | None = None,
    ordering: str = "name",
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    validate_ordering(ordering, allowed_fields={"name", "updated_at", "lifecycle_state"})
    try:
        return list_configurations(
            limit=limit,
            offset=offset,
            search=search,
            enabled=enabled,
            lifecycle_state=lifecycle_state,
            ordering=ordering,
        )
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/discovery", response_model=ResourceDiscoveryResponse)
def signal_jobs_discovery() -> ResourceDiscoveryResponse:
    return resource_discovery(
        resource_id="alpaca-etf-signal-jobs",
        label="ETF Weight Signals",
        item_label="signal Job",
        identity_fields=["uid"],
        searchable_fields=["name", "description"],
        filterable_fields=["enabled", "lifecycle_state"],
        filter_types={"enabled": "boolean"},
        filter_options={
            "lifecycle_state": [
                {"value": "ready", "label": "Ready"},
                {"value": "paused", "label": "Paused"},
                {"value": "provisioning", "label": "Provisioning"},
                {"value": "error", "label": "Error"},
            ]
        },
        orderable_fields=["name", "updated_at", "lifecycle_state"],
        columns=[
            {"id": "name", "header": "Signal", "sortable_key": "name", "hideable": False},
            {"id": "signal_uid", "header": "Signal UID"},
            {"id": "lifecycle_state", "header": "State"},
            {"id": "schedule_type", "header": "Schedule"},
            {"id": "job_image_status", "header": "Image"},
            {"id": "latest_run_status", "header": "Last Run"},
            {
                "id": "latest_run_at",
                "header": "Last Run At",
                "data_type": "datetime",
            },
            {"id": "enabled", "header": "Enabled", "data_type": "boolean"},
        ],
    )


@router.post("", response_model=SignalJobConfigurationResponse, status_code=201)
def signal_job_create(
    request: SignalJobConfigurationCreateRequest = Body(...),
) -> SignalJobConfigurationResponse:
    try:
        return create_configuration(request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{configuration_uid}", response_model=SignalJobConfigurationResponse)
def signal_job_get(configuration_uid: str) -> SignalJobConfigurationResponse:
    item = get_configuration(configuration_uid)
    if item is None:
        raise not_found("Signal Job configuration not found.")
    return item


@router.get(
    "/{configuration_uid}/observations",
    response_model=SignalObservationsResponse,
)
def signal_job_observations(
    configuration_uid: str,
    limit: int = Query(default=100, ge=1, le=100),
) -> SignalObservationsResponse:
    try:
        return get_configuration_observations(configuration_uid, limit=limit)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.patch("/{configuration_uid}", response_model=SignalJobConfigurationResponse)
def signal_job_update(
    configuration_uid: str,
    request: SignalJobConfigurationUpdateRequest = Body(...),
) -> SignalJobConfigurationResponse:
    try:
        return update_configuration(configuration_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.delete("/{configuration_uid}", response_model=dict)
def signal_job_delete(
    configuration_uid: str,
) -> dict:
    try:
        return delete_configuration(configuration_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.get("/{configuration_uid}/runs", response_model=list[SignalJobRunResponse])
def signal_job_runs(
    configuration_uid: str,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[SignalJobRunResponse]:
    try:
        return configuration_runs(configuration_uid, limit=limit)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/{configuration_uid}/actions/run",
    response_model=SignalJobRunAcceptedResponse,
    status_code=202,
)
def signal_job_run(
    configuration_uid: str,
) -> SignalJobRunAcceptedResponse:
    try:
        return run_configuration(configuration_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


def _configuration_action(
    configuration_uid: str,
    operation,
) -> SignalJobConfigurationResponse:
    try:
        return operation(configuration_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/{configuration_uid}/actions/pause",
    response_model=SignalJobConfigurationResponse,
)
def signal_job_pause(
    configuration_uid: str,
) -> SignalJobConfigurationResponse:
    return _configuration_action(configuration_uid, pause_configuration)


@router.post(
    "/{configuration_uid}/actions/resume",
    response_model=SignalJobConfigurationResponse,
)
def signal_job_resume(
    configuration_uid: str,
) -> SignalJobConfigurationResponse:
    return _configuration_action(configuration_uid, resume_configuration)


@router.post(
    "/{configuration_uid}/actions/reconcile",
    response_model=SignalJobConfigurationResponse,
)
def signal_job_reconcile(
    configuration_uid: str,
) -> SignalJobConfigurationResponse:
    return _configuration_action(configuration_uid, reconcile_configuration)


__all__ = ["router"]
