from __future__ import annotations

from alpaca_connectors.account.provider_account_details import project_account_models
from alpaca_connectors.assets.alpaca_asset_details import project_asset_models
from alpaca_connectors.base import ProjectBase
from alpaca_connectors.instances import project_instance_models
from alpaca_connectors.market_data import project_configuration_models, project_storage_models
from alpaca_connectors.operations import project_operation_models
from alpaca_connectors.portfolios import project_portfolio_configuration_models
from alpaca_connectors.settings import BRANCH_ACCESS, PROJECT_NAMESPACE
from metatables.migrations import (
    build_alembic_version_metatable,
    build_metatable_migration_provider,
)


def all_project_metatable_models() -> list[type]:
    """Every project-owned MetaTable managed by this migration provider."""
    return [
        *project_asset_models(),
        *project_storage_models(),
        *project_account_models(),
        *project_configuration_models(),
        *project_operation_models(),
        *project_portfolio_configuration_models(),
        # Contract instances of the Markets time series this application produces (ADR 0047).
        *project_instance_models(),
    ]


PROJECT_TABLE_NAMES = frozenset(model.__table__.name for model in all_project_metatable_models())


# Every table this provider owns and no Markets table (ms-markets 5.0.0): the models are declared on
# ProjectBase and key to Markets by column object, so autogenerate compares only this application's
# tables and needs no include hook. MetaTables 0.1.73 and later never autogenerate a table drop;
# drops are written by hand.
METADATA = ProjectBase.metadata

ProjectAlembicVersion = build_alembic_version_metatable(
    class_name="ProjectAlembicVersion",
    namespace=PROJECT_NAMESPACE,
    identifier="alpaca_connectors.alembic_version",
    schema=None,
    table_name="alpaca_connectors__alembic_version",
)

SHARED_PROVIDER_KWARGS = {
    # Catalog binding key, not an import path: the MetaTables catalog binds this provider's tables
    # to `package:namespace` and rejects a reservation under another key, so it stays "src" after
    # the provider module moved to `alpaca_connectors.migrations`.
    "package": "src",
    "migration_namespace": PROJECT_NAMESPACE,
    "script_location": "alpaca_connectors.migrations:",
    "version_location_prefix": "alpaca_connectors.migrations:versions",
    "alembic_registry": ProjectAlembicVersion,
    # The branch's whole declaration (MetaTables ADR 0029, ADR 0030): this provider is the only one,
    # so what it declares is everything the branch holds.
    "branch_access": BRANCH_ACCESS,
}

migration = build_metatable_migration_provider(
    **SHARED_PROVIDER_KWARGS,
    target_metadata=METADATA,
    metatable_models=all_project_metatable_models(),
)


__all__ = [
    "PROJECT_TABLE_NAMES",
    "ProjectAlembicVersion",
    "SHARED_PROVIDER_KWARGS",
    "all_project_metatable_models",
    "migration",
]
