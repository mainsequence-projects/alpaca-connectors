"""Observable operations: account syncs and Main Sequence JobRuns."""

from __future__ import annotations

from fastapi import APIRouter

from ..errors import api_http_error, not_found
from ..identity import acting_owner
from ..schemas import AccountSyncOperationResponse, JobRunStatusResponse
from ..services.account_sync import get_sync_operation
from ..services.operations import get_job_run

router = APIRouter(prefix="/v1/operations", tags=["Operations"])


@router.get("/job-runs/{job_run_uid}", response_model=JobRunStatusResponse)
def job_run_get(job_run_uid: str) -> JobRunStatusResponse:
    try:
        item = get_job_run(job_run_uid)
    except Exception as exc:
        raise api_http_error(exc) from exc
    if item is None:
        raise not_found("JobRun not found.")
    return item


@router.get(
    "/{operation_uid}",
    response_model=AccountSyncOperationResponse,
    summary="Read an operation again",
    description=(
        "Return an account sync operation to the owner it ran for (the acting owner of "
        "ADR 0019). Any other caller, and an unknown UID, get 404. Asset registration "
        "operations keep their own path under /v1/assets/registration/operations."
    ),
)
def operation_get(operation_uid: str) -> AccountSyncOperationResponse:
    owner = acting_owner()
    try:
        operation = get_sync_operation(operation_uid, owner=owner)
    except Exception as exc:
        raise api_http_error(exc) from exc
    if operation is None:
        raise not_found("Operation not found.")
    return operation


__all__ = ["router"]
