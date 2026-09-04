"""Project State application services."""

from __future__ import annotations

from dataclasses import asdict

from src.account.services import list_account_registrations
from src.market_data import list_market_data_datasets
from src.market_data.storage import ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE
from src.universes import (
    SUPPORTED_COMPONENT_PROVIDERS,
    list_universe_sources,
)

from ..schemas import ProjectConfigurationResponse


def get_project_configuration(
    *, request_user_uid: str | None = None
) -> ProjectConfigurationResponse:
    from mainsequence.code_repository_context import get_code_repository_context

    _, account_count = list_account_registrations(limit=1)
    _, source_count = list_universe_sources(limit=1)
    datasets = list_market_data_datasets()
    context = get_code_repository_context()
    branch = context.code_repository_branch
    return ProjectConfigurationResponse(
        supported_component_providers=list(SUPPORTED_COMPONENT_PROVIDERS),
        migrated_market_data_profiles=[
            "/".join(triple) for triple in sorted(ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE)
        ],
        organization_environment_uid=context.organization_environment_uid,
        organization_environment_name=(
            str(getattr(branch, "organization_environment_name", "") or "").strip() or None
        ),
        request_user_uid=request_user_uid,
        registered_account_count=account_count,
        has_registered_account=account_count > 0,
        universe_source_count=source_count,
        market_data_dataset_count=len(datasets),
        market_data_datasets=[asdict(dataset) for dataset in datasets],
    )
