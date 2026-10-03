"""Observable execution state for Main Sequence JobRuns."""

from __future__ import annotations

from fastapi import APIRouter

from ..errors import api_http_error, not_found
from ..schemas import JobRunStatusResponse
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


__all__ = ["router"]
