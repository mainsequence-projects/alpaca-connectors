"""Project State API routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..capabilities import capability_summaries
from ..schemas import CapabilityCatalogResponse, HealthResponse, ProjectConfigurationResponse
from ..services import get_project_configuration

router = APIRouter(tags=["Project State"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="API health",
    description="Return a minimal health payload for the Alpaca Connectors API.",
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/v1/project-state/capabilities",
    response_model=CapabilityCatalogResponse,
    summary="Implemented capabilities",
    description="Return the repository capabilities and their currently available surfaces.",
)
def capabilities() -> CapabilityCatalogResponse:
    return CapabilityCatalogResponse(capabilities=capability_summaries())


@router.get(
    "/v1/project-state/configuration",
    response_model=ProjectConfigurationResponse,
    summary="Runtime application configuration",
    description="Return live account, universe-source, and migrated dataset catalog state.",
)
def configuration(request: Request) -> ProjectConfigurationResponse:
    return get_project_configuration(
        request_user_uid=getattr(request.state, "user_uid", None),
    )
