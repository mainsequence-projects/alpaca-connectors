from __future__ import annotations

from msm.models import AccountGroupTable, AccountTable
from msm.models.assets.categories import AssetCategoryTable
from msm.models.assets.core import AssetTable

from mainsequence.meta_tables.migrations import (
    build_alembic_version_metatable,
    build_metatable_migration_provider,
    metadata_for_models,
)
from src.account.alpaca_account_details import project_account_models
from src.assets.alpaca_asset_details import project_asset_models
from src.market_data import project_configuration_models, project_storage_models
from src.operations import project_operation_models
from src.universes import project_universe_models


def all_project_metatable_models() -> list[type]:
    """Every project-owned MetaTable managed by this migration provider."""
    return [
        *project_asset_models(),
        *project_storage_models(),
        *project_account_models(),
        *project_universe_models(),
        *project_configuration_models(),
        *project_operation_models(),
    ]


PROJECT_TABLE_NAMES = frozenset(model.__table__.name for model in all_project_metatable_models())


def _include_project_tables(
    name: str | None,
    type_: str,
    parent_names: dict[str, object],
) -> bool:
    """Limit Alembic operations to this provider's project-owned tables."""
    del parent_names
    return type_ != "table" or name in PROJECT_TABLE_NAMES


# SQLAlchemy must see the full foreign-key metadata closure to sort the project tables.
# AssetTable, AccountTable, and AccountGroupTable are metadata-only dependencies: the
# include-name hook excludes them from this provider's DDL and the provider model list excludes
# them from catalog registration. Their schemas remain owned by the ms-markets provider.
METADATA = metadata_for_models(
    [
        AssetTable,
        AssetCategoryTable,
        AccountGroupTable,
        AccountTable,
        *all_project_metatable_models(),
    ]
)

ProjectAlembicVersion = build_alembic_version_metatable(
    class_name="ProjectAlembicVersion",
    namespace="alpaca-connectors",
    identifier="alpaca_connectors.alembic_version",
    schema=None,
    table_name="alpaca_connectors__alembic_version",
)

migration = build_metatable_migration_provider(
    package="src",
    migration_namespace="alpaca-connectors",
    script_location="src.migrations:",
    version_location_prefix="src.migrations:versions",
    target_metadata=METADATA,
    alembic_registry=ProjectAlembicVersion,
    metatable_models=all_project_metatable_models(),
    include_name_hook=_include_project_tables,
)


__all__ = [
    "PROJECT_TABLE_NAMES",
    "ProjectAlembicVersion",
    "all_project_metatable_models",
    "migration",
]
