"""Asset-universe registration, configuration, and materialization capability.

Its modules load only when one of their names is used (ADR 0027): the tables of Universe Sources
and Asset Universes need no ETF extractor, so importing this package or those two modules never
loads it.
"""

from __future__ import annotations

from importlib import import_module

_LAZY_IMPORTS = {
    name: f"alpaca_connectors.universes.{module}"
    for module, names in {
        "etf_holdings": (
            "SUPPORTED_COMPONENT_PROVIDERS",
            "build_holdings_asset_category_plan",
            "build_holdings_asset_category_unique_identifier",
        ),
        "materialized": (
            "MATERIALIZED_UNIVERSE_PREFIX",
            "create_asset_universe_configuration",
            "delete_asset_universe",
            "get_asset_universe_view",
            "list_asset_universes",
            "require_asset_universe_links",
            "update_asset_universe",
        ),
        "registry": (
            "AssetUniverse",
            "AssetUniverseTable",
            "create_asset_universe",
            "get_asset_universe",
            "get_asset_universe_by_category_uid",
            "get_asset_universe_by_source_uid",
            "project_asset_universe_models",
            "update_asset_universe_state",
        ),
        "services": (
            "AssetUniverseRunPlan",
            "AssetUniverseRunResult",
            "asset_identifiers_by_symbol_for_universe_plan",
            "asset_identifiers_for_universe_plan",
            "materialize_asset_universe",
            "preview_asset_universe",
            "preview_universe_source",
            "run_asset_universe",
        ),
        "sources": (
            "UniverseSource",
            "UniverseSourceTable",
            "create_universe_source",
            "delete_universe_source",
            "get_universe_source",
            "list_universe_sources",
            "load_default_universe_sources",
            "project_universe_source_models",
            "seed_default_universe_sources",
            "update_universe_source",
        ),
    }.items()
    for name in names
}


def project_universe_models() -> list[type]:
    """Project-owned universe MetaTables in parent-before-child order."""
    from .registry import project_asset_universe_models
    from .sources import project_universe_source_models

    return [
        *project_universe_source_models(),
        *project_asset_universe_models(),
    ]


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        value = getattr(import_module(_LAZY_IMPORTS[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = sorted([*_LAZY_IMPORTS, "project_universe_models"])
