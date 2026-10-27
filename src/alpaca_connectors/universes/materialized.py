"""Read and manage AssetUniverse registrations and their materialized categories."""

from __future__ import annotations

import uuid
from typing import Any

from alpaca_connectors.universes.registry import (
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

    from alpaca_connectors.universes.sources import get_universe_source

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


def _universe_rows(universe_uid: uuid.UUID | str | None = None) -> list[dict[str, Any]]:
    """Registered universes joined with their sources: one query of this project's tables."""
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from alpaca_connectors.runtime import start_markets_engine
    from alpaca_connectors.universes.sources import UniverseSourceTable

    runtime = start_markets_engine()
    statement = select(
        AssetUniverseTable.uid.label("uid"),
        AssetUniverseTable.source_uid.label("source_uid"),
        AssetUniverseTable.asset_category_uid.label("asset_category_uid"),
        AssetUniverseTable.is_active.label("is_active"),
        AssetUniverseTable.created_at.label("created_at"),
        AssetUniverseTable.updated_at.label("updated_at"),
        UniverseSourceTable.name.label("source_name"),
        UniverseSourceTable.symbol.label("symbol"),
        UniverseSourceTable.source_url.label("source_url"),
    ).join(UniverseSourceTable, AssetUniverseTable.source_uid == UniverseSourceTable.uid)
    if universe_uid is not None:
        statement = statement.where(AssetUniverseTable.uid == uuid.UUID(str(universe_uid)))
    operation = compile_markets_statement(statement, context=runtime.context, operation="select")
    return operation_result_rows(execute_markets_operation(operation, context=runtime.context))


def _categories_by_uid(category_uids: list[str]) -> dict[str, Any]:
    from msm.api.assets import AssetCategory

    from alpaca_connectors.markets_api import read_in_batches

    return {
        str(category.uid): category
        for category in read_in_batches(
            lambda batch: AssetCategory.filter(uid__in=batch, limit=len(batch)), category_uids
        )
    }


def _member_counts(category_uids: list[str]) -> dict[str, int]:
    """Membership count per category, from the categories' memberships read in pages.

    Markets has no grouped membership count, so this reads the memberships of these categories
    (one ``count`` and one ``filter`` per 500 memberships) and counts them here.
    """
    from msm.api.assets import AssetCategoryMembership

    from alpaca_connectors.markets_api import read_all_pages

    if not category_uids:
        return {}
    total = AssetCategoryMembership.count(category_uid__in=category_uids)
    rows = read_all_pages(
        lambda limit, offset: AssetCategoryMembership.filter(
            category_uid__in=category_uids,
            order_by=["category_uid", "asset_uid"],
            limit=limit,
            offset=offset,
        ),
        total,
    )
    counts = dict.fromkeys(category_uids, 0)
    for row in rows:
        counts[str(row.category_uid)] = counts.get(str(row.category_uid), 0) + 1
    return counts


def _universe_view(row: dict[str, Any], category: Any | None, asset_count: int) -> dict[str, Any]:
    category_uid = str(row["asset_category_uid"])
    display_name = getattr(category, "display_name", None)
    description = getattr(category, "description", None)
    return {
        "uid": str(row["uid"]),
        "source_uid": str(row["source_uid"]),
        "asset_category_uid": category_uid,
        "display_name": display_name,
        "symbol": row["symbol"],
        "source_url": row["source_url"],
        "description": description,
        "is_active": bool(row["is_active"]),
        "asset_count": int(asset_count),
        "asset_category": {
            "uid": category_uid,
            "unique_identifier": getattr(category, "unique_identifier", None),
            "display_name": display_name,
            "description": description,
        },
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def get_asset_universe_view(universe_uid: uuid.UUID | str) -> dict[str, Any] | None:
    """Return one Universe and its linked category without loading every member Asset.

    The universe and its source come from this project's tables; the category and its member
    count from Markets (ms-markets ADR 0047).
    """
    from msm.api.assets import AssetCategory, AssetCategoryMembership

    rows = _universe_rows(universe_uid)
    if not rows:
        return None
    row = rows[0]
    category = AssetCategory.get_by_uid(row["asset_category_uid"])
    if category is None:
        return None
    count = AssetCategoryMembership.count(category_uid=str(row["asset_category_uid"]))
    return _universe_view(row, category, count)


def list_asset_universes(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    ordering: str = "display_name",
) -> tuple[list[dict[str, Any]], int]:
    """List registered universes by their own UID, enriched from source/category rows.

    The universes and sources are this project's tables (one universe per ETF source, so the
    set is small); their categories are read from Markets in one batch, so search and ordering
    on the category's display name happen here. Member counts are read for the page only.
    """
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    if ordering_key not in {"display_name", "symbol", "updated_at"}:
        raise ValueError(f"Unsupported universe ordering {ordering!r}.")
    rows = _universe_rows()
    categories = _categories_by_uid([str(row["asset_category_uid"]) for row in rows])
    # A universe whose category no longer exists is not listed, as the inner join did.
    rows = [row for row in rows if str(row["asset_category_uid"]) in categories]
    if search:
        needle = search.strip().casefold()
        rows = [
            row
            for row in rows
            if any(
                needle in str(value or "").casefold()
                for value in (
                    row.get("source_name"),
                    row.get("symbol"),
                    categories[str(row["asset_category_uid"])].display_name,
                )
            )
        ]

    def sort_value(row: dict[str, Any]) -> Any:
        if ordering_key == "display_name":
            return str(categories[str(row["asset_category_uid"])].display_name or "")
        return row.get(ordering_key) or ""

    rows.sort(key=lambda row: str(row["uid"]))
    rows.sort(key=sort_value, reverse=descending)
    total = len(rows)
    page = rows[offset : offset + limit]
    counts = _member_counts([str(row["asset_category_uid"]) for row in page])
    items = []
    for row in page:
        category_uid = str(row["asset_category_uid"])
        view = _universe_view(row, categories[category_uid], counts.get(category_uid, 0))
        view.pop("asset_category")
        items.append(view)
    return items, total


def create_asset_universe_configuration(
    *,
    name: str,
    symbol: str,
    source_url: str,
) -> dict[str, Any]:
    """Register explicit source/category links without extracting holdings."""
    from msm.api.assets import AssetCategory

    from alpaca_connectors.runtime import start_markets_engine
    from alpaca_connectors.universes.etf_holdings import (
        build_holdings_asset_category_unique_identifier,
    )
    from alpaca_connectors.universes.sources import (
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

    from alpaca_connectors.market_data.configurations import bar_configurations_for_universe
    from alpaca_connectors.operations.signal_job_configurations import (
        signal_job_configurations_for_universe,
    )

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
    # Empty the category's memberships through Markets, then delete the registration and the
    # category it owns.
    AssetCategory.replace_memberships(category_uid=category_uid, asset_uids=[])
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
