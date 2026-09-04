"""Read and manage AssetUniverse registrations and their materialized categories."""

from __future__ import annotations

import uuid
from typing import Any

from src.universes.registry import (
    AssetUniverse,
    AssetUniverseTable,
    create_asset_universe,
    get_asset_universe,
    get_asset_universe_by_source_uid,
    update_asset_universe_state,
)

MATERIALIZED_UNIVERSE_PREFIX = "HOLDINGS__"


def require_asset_universe_links(universe_uid: uuid.UUID | str) -> tuple[Any, Any, Any]:
    """Return the registered universe, its explicit source, and its category."""
    from msm.api.assets import AssetCategory

    from src.universes.sources import get_universe_source

    universe = get_asset_universe(universe_uid)
    if universe is None:
        raise LookupError(f"Asset Universe {universe_uid!s} does not exist.")
    source = get_universe_source(universe.source_uid)
    if source is None:
        raise RuntimeError(
            f"Asset Universe {universe_uid!s} references missing source {universe.source_uid!s}."
        )
    category = AssetCategory.get_by_uid(universe.asset_category_uid)
    if category is None:
        raise RuntimeError(
            "Asset Universe "
            f"{universe_uid!s} references missing AssetCategory "
            f"{universe.asset_category_uid!s}."
        )
    return universe, source, category


def get_asset_universe_view(universe_uid: uuid.UUID | str) -> dict[str, Any] | None:
    """Return one Universe and its linked category without loading every member Asset."""
    from msm.api.base import operation_result_rows
    from msm.models import AssetCategoryMembershipTable, AssetCategoryTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, select

    from src.runtime import start_markets_engine
    from src.universes.sources import UniverseSourceTable

    runtime = start_markets_engine()
    models = [
        UniverseSourceTable,
        AssetCategoryTable,
        AssetCategoryMembershipTable,
        AssetUniverseTable,
    ]
    statement = (
        select(
            AssetUniverseTable.uid.label("uid"),
            AssetUniverseTable.source_uid.label("source_uid"),
            AssetUniverseTable.asset_category_uid.label("asset_category_uid"),
            AssetUniverseTable.is_active.label("is_active"),
            AssetUniverseTable.created_at.label("created_at"),
            AssetUniverseTable.updated_at.label("updated_at"),
            UniverseSourceTable.symbol.label("symbol"),
            UniverseSourceTable.source_url.label("source_url"),
            AssetCategoryTable.unique_identifier.label("category_unique_identifier"),
            AssetCategoryTable.display_name.label("display_name"),
            AssetCategoryTable.description.label("description"),
            func.count(AssetCategoryMembershipTable.asset_uid).label("asset_count"),
        )
        .select_from(AssetUniverseTable)
        .join(UniverseSourceTable, AssetUniverseTable.source_uid == UniverseSourceTable.uid)
        .join(
            AssetCategoryTable,
            AssetUniverseTable.asset_category_uid == AssetCategoryTable.uid,
        )
        .outerjoin(
            AssetCategoryMembershipTable,
            AssetCategoryMembershipTable.category_uid == AssetCategoryTable.uid,
        )
        .where(AssetUniverseTable.uid == uuid.UUID(str(universe_uid)))
        .group_by(
            AssetUniverseTable.uid,
            UniverseSourceTable.symbol,
            UniverseSourceTable.source_url,
            AssetCategoryTable.uid,
        )
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
        models=models,
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(operation, context=runtime.context))
    if not rows:
        return None
    row = rows[0]
    return {
        "uid": str(row["uid"]),
        "source_uid": str(row["source_uid"]),
        "asset_category_uid": str(row["asset_category_uid"]),
        "display_name": row["display_name"],
        "symbol": row["symbol"],
        "source_url": row["source_url"],
        "description": row.get("description"),
        "is_active": bool(row["is_active"]),
        "asset_count": int(row.get("asset_count", 0)),
        "asset_category": {
            "uid": str(row["asset_category_uid"]),
            "unique_identifier": row["category_unique_identifier"],
            "display_name": row["display_name"],
            "description": row.get("description"),
        },
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_asset_universes(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    ordering: str = "display_name",
) -> tuple[list[dict[str, Any]], int]:
    """List registered universes by their own UID, enriched from source/category rows."""
    from msm.api.base import operation_result_rows
    from msm.bootstrap import resolve_runtime
    from msm.models.assets.categories import (
        AssetCategoryMembershipTable,
        AssetCategoryTable,
    )
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    from src.runtime import start_markets_engine
    from src.universes.sources import UniverseSourceTable

    start_markets_engine()
    runtime = resolve_runtime(
        models=[
            UniverseSourceTable,
            AssetCategoryTable,
            AssetCategoryMembershipTable,
            AssetUniverseTable,
        ],
        row_model_name="AssetUniverse",
    )
    statement = (
        select(
            AssetUniverseTable.uid.label("uid"),
            AssetUniverseTable.source_uid.label("source_uid"),
            AssetUniverseTable.asset_category_uid.label("asset_category_uid"),
            AssetUniverseTable.is_active.label("is_active"),
            AssetUniverseTable.created_at.label("created_at"),
            AssetUniverseTable.updated_at.label("updated_at"),
            UniverseSourceTable.symbol.label("symbol"),
            UniverseSourceTable.source_url.label("source_url"),
            AssetCategoryTable.display_name.label("display_name"),
            AssetCategoryTable.description.label("description"),
            func.count(AssetCategoryMembershipTable.asset_uid).label("asset_count"),
        )
        .join(UniverseSourceTable, AssetUniverseTable.source_uid == UniverseSourceTable.uid)
        .join(
            AssetCategoryTable,
            AssetUniverseTable.asset_category_uid == AssetCategoryTable.uid,
        )
        .outerjoin(
            AssetCategoryMembershipTable,
            AssetCategoryMembershipTable.category_uid == AssetCategoryTable.uid,
        )
    )
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                UniverseSourceTable.name.ilike(pattern),
                UniverseSourceTable.symbol.ilike(pattern),
                AssetCategoryTable.display_name.ilike(pattern),
            )
        )
    statement = statement.group_by(
        AssetUniverseTable.uid,
        UniverseSourceTable.symbol,
        UniverseSourceTable.source_url,
        AssetCategoryTable.uid,
    )
    count_statement = select(func.count().label("count")).select_from(statement.subquery())
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "display_name": AssetCategoryTable.display_name,
        "symbol": UniverseSourceTable.symbol,
        "updated_at": AssetUniverseTable.updated_at,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported universe ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    statement = statement.order_by(ordering_expression, AssetUniverseTable.uid.asc())
    models = [
        UniverseSourceTable,
        AssetCategoryTable,
        AssetCategoryMembershipTable,
        AssetUniverseTable,
    ]
    page_operation = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
        models=models,
        access="read",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=models,
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(page_operation, context=runtime.context))
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    items = [
        {
            **row,
            "uid": str(row["uid"]),
            "source_uid": str(row["source_uid"]),
            "asset_category_uid": str(row["asset_category_uid"]),
            "asset_count": int(row.get("asset_count", 0)),
        }
        for row in rows
    ]
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    return items, total


def create_asset_universe_configuration(
    *,
    name: str,
    symbol: str,
    source_url: str,
) -> dict[str, Any]:
    """Register explicit source/category links without extracting holdings."""
    from msm.api.assets import AssetCategory

    from src.runtime import start_markets_engine
    from src.universes.etf_holdings import build_holdings_asset_category_unique_identifier
    from src.universes.sources import (
        UniverseSource,
        create_universe_source,
        list_universe_sources,
        normalize_source_values,
        update_universe_source,
    )

    normalized = normalize_source_values(name=name, symbol=symbol, source_url=source_url)
    start_markets_engine()
    category_identifier = build_holdings_asset_category_unique_identifier(normalized["symbol"])
    if AssetCategory.get_by_unique_identifier(category_identifier) is not None:
        raise ValueError(f"AssetCategory for {normalized['symbol']} already exists.")

    sources, _ = list_universe_sources(search=normalized["symbol"], limit=100)
    source: UniverseSource | None = next(
        (
            candidate
            for candidate in sources
            if candidate.symbol == normalized["symbol"]
            and candidate.source_url == normalized["source_url"]
        ),
        None,
    )
    created_source = source is None
    if source is None:
        source = create_universe_source(**normalized, enabled=True)
    elif get_asset_universe_by_source_uid(source.uid) is not None:
        raise ValueError(f"Universe source {source.uid!s} already has a registered Asset Universe.")
    elif not source.enabled:
        source = update_universe_source(source.uid, enabled=True)

    category = None
    universe: AssetUniverse | None = None
    try:
        category = AssetCategory.create(
            unique_identifier=category_identifier,
            display_name=normalized["name"],
            description=f"Configured holdings universe for ETF {normalized['symbol']}.",
            metadata_json=None,
        )
        universe = create_asset_universe(
            source_uid=source.uid,
            asset_category_uid=category.uid,
            is_active=True,
        )
        created = get_asset_universe_view(universe.uid)
        if created is None:
            raise RuntimeError("Created Asset Universe could not be read back.")
        return created
    except Exception:
        if universe is not None:
            AssetUniverse.delete(universe.uid)
        if category is not None:
            AssetCategory.delete(category.uid)
        if created_source:
            UniverseSource.delete(source.uid)
        raise


def update_asset_universe(
    universe_uid: uuid.UUID | str,
    *,
    display_name: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
) -> dict[str, Any]:
    """Update category presentation and universe lifecycle without metadata JSON."""
    from msm.api.assets import AssetCategory

    existing = get_asset_universe_view(universe_uid)
    if existing is None:
        raise LookupError(f"Asset Universe {universe_uid!s} does not exist.")
    category_values = {
        key: value
        for key, value in {
            "display_name": display_name,
            "description": description,
        }.items()
        if value is not None
    }
    if category_values:
        AssetCategory.update(existing["asset_category_uid"], category_values)
    if is_active is not None:
        update_asset_universe_state(universe_uid, is_active=is_active)
    updated = get_asset_universe_view(universe_uid)
    if updated is None:
        raise RuntimeError("Updated Asset Universe could not be read back.")
    return updated


def delete_asset_universe(universe_uid: uuid.UUID | str) -> dict[str, Any]:
    """Delete one registered universe and its materialization, retaining its source."""
    from msm.api.assets import AssetCategory
    from msm.models import AssetCategoryMembershipTable
    from msm.repositories.asset_categories import (
        build_delete_asset_category_memberships_for_category_operation,
    )
    from msm.repositories.base import execute_markets_operation

    from src.market_data.configurations import bar_configurations_for_universe
    from src.operations.signal_job_configurations import signal_job_configurations_for_universe
    from src.runtime import start_markets_engine

    universe = get_asset_universe_view(universe_uid)
    if universe is None:
        raise LookupError(f"Asset Universe {universe_uid!s} does not exist.")
    dependent_configurations = bar_configurations_for_universe(universe_uid)
    if dependent_configurations:
        dependencies = ", ".join(
            f"{configuration.name} ({configuration.uid!s})"
            for configuration in dependent_configurations
        )
        raise ValueError(
            f"Asset Universe {universe_uid!s} is referenced by bar configuration(s): "
            f"{dependencies}. Delete or change those configurations first."
        )
    dependent_signal_configurations = signal_job_configurations_for_universe(universe_uid)
    if dependent_signal_configurations:
        dependencies = ", ".join(
            f"{configuration.name} ({configuration.uid!s})"
            for configuration in dependent_signal_configurations
        )
        raise ValueError(
            f"Asset Universe {universe_uid!s} is referenced by signal Job configuration(s): "
            f"{dependencies}. Delete or change those configurations first."
        )

    category_uid = universe["asset_category_uid"]
    runtime = start_markets_engine()
    delete_memberships = build_delete_asset_category_memberships_for_category_operation(
        runtime.context,
        category_uid=category_uid,
    )
    execute_markets_operation(delete_memberships, context=runtime.context)
    AssetUniverse.delete(universe_uid)
    AssetCategory.delete(category_uid)
    return {
        "universe_uid": str(universe_uid),
        "source_uid": universe["source_uid"],
        "asset_category_uid": category_uid,
        "deleted": True,
        "deleted_memberships": int(universe["asset_count"]),
    }


__all__ = [
    "MATERIALIZED_UNIVERSE_PREFIX",
    "create_asset_universe_configuration",
    "delete_asset_universe",
    "get_asset_universe_view",
    "list_asset_universes",
    "require_asset_universe_links",
    "update_asset_universe",
]
