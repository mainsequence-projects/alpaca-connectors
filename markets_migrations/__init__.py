from __future__ import annotations

from mainsequence.meta_tables.migrations import (
    build_alembic_version_metatable,
    build_metatable_migration_provider,
)

from src.markets_storage.alpaca_bars import METADATA
from src.markets_storage.alpaca_bars import project_storage_models


ProjectAlembicVersion = build_alembic_version_metatable(
    class_name="ProjectAlembicVersion",
    namespace='alpaca-connectors',
    identifier='alpaca_connectors.alembic_version',
    schema=None,
    table_name='alpaca_connectors__alembic_version',
)

migration = build_metatable_migration_provider(
    package='src',
    migration_namespace='alpaca-connectors',
    script_location="markets_migrations:",
    version_location_prefix="markets_migrations:versions",
    target_metadata=METADATA,
    alembic_registry=ProjectAlembicVersion,
    metatable_models=project_storage_models(),
)


__all__ = ["ProjectAlembicVersion", "migration"]
