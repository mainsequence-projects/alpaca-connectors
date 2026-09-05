"""API shaping for durable Alpaca bar configurations and update actions."""

from __future__ import annotations

from src.market_data import (
    create_bar_configuration,
    delete_bar_configuration,
    get_bar_configuration,
    list_bar_configurations,
    resolve_market_data_update,
    update_bar_configuration,
)
from src.operations import launch_alpaca_bars_update

from ..schemas import (
    BarConfigurationCreateRequest,
    BarConfigurationResolutionResponse,
    BarConfigurationResolveRequest,
    BarConfigurationResponse,
    BarConfigurationUpdateAcceptedResponse,
    BarConfigurationUpdateRequest,
)
from .common import collection_response


def _configuration_response(row: object) -> BarConfigurationResponse:
    """Project the reusable typed row into the API-owned response model."""

    if not hasattr(row, "model_dump"):
        raise TypeError("Bar configuration rows must provide model_dump().")
    return BarConfigurationResponse.model_validate(row.model_dump(mode="json"))


def list_configurations(
    *,
    limit: int,
    offset: int,
    search: str | None,
    enabled: bool | None,
    asset_source: str | None,
    ordering: str,
):
    items, total = list_bar_configurations(
        limit=limit,
        offset=offset,
        search=search,
        enabled=enabled,
        asset_source=asset_source,
        ordering=ordering,
    )
    return collection_response(items=items, total=total, limit=limit, offset=offset)


def create_configuration(
    request: BarConfigurationCreateRequest,
) -> BarConfigurationResponse:
    return _configuration_response(create_bar_configuration(**request.model_dump()))


def get_configuration(configuration_uid: str) -> BarConfigurationResponse | None:
    row = get_bar_configuration(configuration_uid)
    return _configuration_response(row) if row else None


def update_configuration(
    configuration_uid: str,
    request: BarConfigurationUpdateRequest,
) -> BarConfigurationResponse:
    return _configuration_response(
        update_bar_configuration(
            configuration_uid,
            **request.model_dump(exclude_unset=True),
        )
    )


def delete_configuration(configuration_uid: str) -> dict:
    return delete_bar_configuration(configuration_uid)


def resolve_configuration(
    configuration_uid: str,
    request: BarConfigurationResolveRequest,
) -> BarConfigurationResolutionResponse:
    return BarConfigurationResolutionResponse.model_validate(
        resolve_market_data_update(
            configuration_uid=configuration_uid,
            hash_namespace=request.hash_namespace,
        )
    )


def submit_configuration_update(
    configuration_uid: str,
) -> BarConfigurationUpdateAcceptedResponse:
    submission = launch_alpaca_bars_update(configuration_uid)
    return BarConfigurationUpdateAcceptedResponse(
        configuration_uid=submission.configuration_uid,
        job_uid=submission.job_uid,
        job_run_uid=submission.job_run_uid,
        status=submission.status,
        status_url=f"/v1/operations/job-runs/{submission.job_run_uid}",
    )


__all__ = [
    "create_configuration",
    "delete_configuration",
    "get_configuration",
    "list_configurations",
    "resolve_configuration",
    "submit_configuration_update",
    "update_configuration",
]
