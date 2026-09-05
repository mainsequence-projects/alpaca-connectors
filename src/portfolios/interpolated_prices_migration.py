"""Configured dynamic migration provider for persistent portfolio interpolation storage."""

from __future__ import annotations

from msm.models import AssetTable

from mainsequence.meta_tables.migrations import (
    build_metatable_migration_provider,
    metadata_for_models,
)
from src.migrations import SHARED_PROVIDER_KWARGS
from src.portfolios.interpolated_prices_schema import dynamic_storage_models_from_env

DYNAMIC_INTERPOLATED_PRICES_STORAGES = dynamic_storage_models_from_env()
_DYNAMIC_TABLE_NAMES = {
    model.__table__.name for model in DYNAMIC_INTERPOLATED_PRICES_STORAGES
}


def _include_dynamic_objects(object_, name, type_, reflected, compare_to):
    del reflected, compare_to
    if type_ == "table":
        return name in _DYNAMIC_TABLE_NAMES
    table = getattr(object_, "table", None)
    table_name = getattr(table, "name", None)
    return table_name is None or table_name in _DYNAMIC_TABLE_NAMES


migration = build_metatable_migration_provider(
    **SHARED_PROVIDER_KWARGS,
    target_metadata=metadata_for_models(
        [*DYNAMIC_INTERPOLATED_PRICES_STORAGES, AssetTable]
    ),
    metatable_models=DYNAMIC_INTERPOLATED_PRICES_STORAGES,
    include_object_hook=_include_dynamic_objects,
)


__all__ = ["DYNAMIC_INTERPOLATED_PRICES_STORAGES", "migration"]
