"""Asset-universe registration, configuration, and materialization capability."""

from .etf_holdings import (
    SUPPORTED_COMPONENT_PROVIDERS,
    build_holdings_asset_category_plan,
    build_holdings_asset_category_unique_identifier,
)
from .materialized import (
    MATERIALIZED_UNIVERSE_PREFIX,
    create_asset_universe_configuration,
    delete_asset_universe,
    get_asset_universe_view,
    list_asset_universes,
    require_asset_universe_links,
    update_asset_universe,
)
from .registry import (
    AssetUniverse,
    AssetUniverseTable,
    create_asset_universe,
    get_asset_universe,
    get_asset_universe_by_category_uid,
    get_asset_universe_by_source_uid,
    project_asset_universe_models,
    update_asset_universe_state,
)
from .services import (
    AssetUniverseRunPlan,
    AssetUniverseRunResult,
    asset_identifiers_by_symbol_for_universe_plan,
    asset_identifiers_for_universe_plan,
    materialize_asset_universe,
    preview_asset_universe,
    preview_universe_source,
    run_asset_universe,
)
from .sources import (
    UniverseSource,
    UniverseSourceTable,
    create_universe_source,
    delete_universe_source,
    get_universe_source,
    list_universe_sources,
    load_default_universe_sources,
    project_universe_source_models,
    seed_default_universe_sources,
    update_universe_source,
)


def project_universe_models() -> list[type]:
    """Project-owned universe MetaTables in parent-before-child order."""
    return [
        *project_universe_source_models(),
        *project_asset_universe_models(),
    ]


__all__ = [
    "MATERIALIZED_UNIVERSE_PREFIX",
    "SUPPORTED_COMPONENT_PROVIDERS",
    "AssetUniverse",
    "AssetUniverseRunPlan",
    "AssetUniverseRunResult",
    "AssetUniverseTable",
    "UniverseSource",
    "UniverseSourceTable",
    "asset_identifiers_by_symbol_for_universe_plan",
    "asset_identifiers_for_universe_plan",
    "build_holdings_asset_category_plan",
    "build_holdings_asset_category_unique_identifier",
    "create_asset_universe",
    "create_asset_universe_configuration",
    "create_universe_source",
    "delete_asset_universe",
    "delete_universe_source",
    "get_asset_universe",
    "get_asset_universe_by_category_uid",
    "get_asset_universe_by_source_uid",
    "get_asset_universe_view",
    "get_universe_source",
    "list_asset_universes",
    "list_universe_sources",
    "load_default_universe_sources",
    "materialize_asset_universe",
    "preview_asset_universe",
    "preview_universe_source",
    "project_universe_models",
    "require_asset_universe_links",
    "run_asset_universe",
    "seed_default_universe_sources",
    "update_asset_universe",
    "update_asset_universe_state",
    "update_universe_source",
]
