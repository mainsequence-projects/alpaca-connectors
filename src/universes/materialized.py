"""Read and manage materialized ms-markets AssetCategory universes."""

from __future__ import annotations

from typing import Any

MATERIALIZED_UNIVERSE_PREFIX = "HOLDINGS__"
MATERIALIZED_UNIVERSE_METADATA_NAMESPACE = "alpaca_connectors"


def _is_managed_identifier(identifier: str) -> bool:
    return identifier.startswith(MATERIALIZED_UNIVERSE_PREFIX)


def materialized_universe_is_active(metadata_json: Any) -> bool:
    """Return the connector-owned lifecycle state stored in category metadata.

    Existing categories predate lifecycle management, so a missing flag remains active.
    """
    if not isinstance(metadata_json, dict):
        return True
    connector_metadata = metadata_json.get(MATERIALIZED_UNIVERSE_METADATA_NAMESPACE)
    if not isinstance(connector_metadata, dict):
        return True
    active = connector_metadata.get("active")
    return active if isinstance(active, bool) else True


def materialized_universe_source_uid(metadata_json: Any) -> str | None:
    if not isinstance(metadata_json, dict):
        return None
    connector_metadata = metadata_json.get(MATERIALIZED_UNIVERSE_METADATA_NAMESPACE)
    if not isinstance(connector_metadata, dict):
        return None
    source_uid = connector_metadata.get("source_uid")
    return str(source_uid) if source_uid else None


def _metadata_with_active_state(metadata_json: Any, *, is_active: bool) -> dict[str, Any]:
    metadata = dict(metadata_json) if isinstance(metadata_json, dict) else {}
    connector_metadata = metadata.get(MATERIALIZED_UNIVERSE_METADATA_NAMESPACE)
    connector_metadata = dict(connector_metadata) if isinstance(connector_metadata, dict) else {}
    connector_metadata["active"] = is_active
    metadata[MATERIALIZED_UNIVERSE_METADATA_NAMESPACE] = connector_metadata
    return metadata


def get_materialized_universe(category_uid: str) -> dict[str, Any] | None:
    from msm.api.assets import Asset, AssetCategory, AssetCategoryMembership

    from src.runtime import start_markets_engine

    start_markets_engine()
    category = AssetCategory.get_by_uid(category_uid)
    if category is None or not _is_managed_identifier(category.unique_identifier):
        return None
    memberships = AssetCategoryMembership.filter(category_uid=category_uid, limit=10_000)
    assets = [Asset.get_by_uid(membership.asset_uid) for membership in memberships]
    return {
        **category.model_dump(mode="json"),
        "uid": str(category.uid),
        "is_active": materialized_universe_is_active(category.metadata_json),
        "source_uid": materialized_universe_source_uid(category.metadata_json),
        "asset_uids": [str(asset.uid) for asset in assets if asset is not None],
        "asset_identifiers": [asset.unique_identifier for asset in assets if asset is not None],
        "asset_count": len(memberships),
    }


def list_materialized_universes(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    ordering: str = "display_name",
) -> tuple[list[dict[str, Any]], int]:
    from msm.api.assets import AssetCategory
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    from src.runtime import start_markets_engine

    runtime = start_markets_engine()
    model = AssetCategory.__table__
    statement = select(model).where(
        model.unique_identifier.startswith(MATERIALIZED_UNIVERSE_PREFIX)
    )
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                model.display_name.ilike(pattern),
                model.unique_identifier.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(statement.subquery())
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "display_name": model.display_name,
        "unique_identifier": model.unique_identifier,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported universe ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    page_statement = statement.order_by(ordering_expression, model.uid.asc())
    page_statement = page_statement.limit(limit).offset(offset)
    page_operation = compile_markets_statement(
        page_statement,
        context=runtime.context,
        operation="select",
        models=[model],
        access="read",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[model],
        access="read",
    )
    category_rows = operation_result_rows(
        execute_markets_operation(page_operation, context=runtime.context)
    )
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    items = [get_materialized_universe(str(category["uid"])) for category in category_rows]
    return [item for item in items if item is not None], total


def create_materialized_universe_configuration(
    *,
    name: str,
    symbol: str,
    source_url: str,
) -> dict[str, Any]:
    """Create universe identity and source configuration without running extraction."""
    from msm.api.assets import AssetCategory

    from src.runtime import start_markets_engine
    from src.universes.etf_holdings import build_holdings_asset_category_unique_identifier
    from src.universes.sources import (
        create_universe_source,
        list_universe_sources,
        normalize_source_values,
        update_universe_source,
    )

    normalized = normalize_source_values(name=name, symbol=symbol, source_url=source_url)
    start_markets_engine()
    unique_identifier = build_holdings_asset_category_unique_identifier(normalized["symbol"])
    if AssetCategory.get_by_unique_identifier(unique_identifier) is not None:
        raise ValueError(f"Universe for {normalized['symbol']} already exists.")

    sources, _ = list_universe_sources(search=normalized["symbol"], limit=100)
    source = next(
        (
            candidate
            for candidate in sources
            if candidate.symbol == normalized["symbol"]
            and candidate.source_url == normalized["source_url"]
        ),
        None,
    )
    if source is None:
        source = create_universe_source(**normalized, enabled=True)
    elif not source.enabled:
        source = update_universe_source(source.uid, enabled=True)

    metadata_json = {
        MATERIALIZED_UNIVERSE_METADATA_NAMESPACE: {
            "active": True,
            "source_uid": str(source.uid),
        }
    }
    category = AssetCategory.create(
        unique_identifier=unique_identifier,
        display_name=normalized["name"],
        description=f"Configured holdings universe for ETF {normalized['symbol']}.",
        metadata_json=metadata_json,
    )
    created = get_materialized_universe(str(category.uid))
    if created is None:
        raise RuntimeError("Created universe could not be read back.")
    return created


def update_materialized_universe(
    category_uid: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
    metadata_json: dict[str, Any] | None = None,
    is_active: bool | None = None,
) -> dict[str, Any]:
    from msm.api.assets import AssetCategory

    existing = get_materialized_universe(category_uid)
    if existing is None:
        raise LookupError(f"AssetCategory {category_uid} does not exist.")
    values = {
        key: value
        for key, value in {
            "display_name": display_name,
            "description": description,
            "metadata_json": metadata_json,
        }.items()
        if value is not None
    }
    if is_active is not None:
        metadata_base = metadata_json if metadata_json is not None else existing.get("metadata_json")
        values["metadata_json"] = _metadata_with_active_state(
            metadata_base,
            is_active=is_active,
        )
    if values:
        AssetCategory.update(category_uid, values)
    updated = get_materialized_universe(category_uid)
    if updated is None:
        raise RuntimeError("Updated AssetCategory could not be read back.")
    return updated


def delete_materialized_universe(category_uid: str) -> dict[str, Any]:
    from msm.api.assets import AssetCategory, AssetCategoryMembership

    universe = get_materialized_universe(category_uid)
    if universe is None:
        raise LookupError(f"AssetCategory {category_uid} does not exist.")
    memberships = AssetCategoryMembership.filter(category_uid=category_uid, limit=10_000)
    for membership in memberships:
        AssetCategoryMembership.delete(membership.uid)
    AssetCategory.delete(category_uid)
    return {
        "category_uid": category_uid,
        "deleted": True,
        "deleted_memberships": len(memberships),
    }


__all__ = [
    "MATERIALIZED_UNIVERSE_METADATA_NAMESPACE",
    "MATERIALIZED_UNIVERSE_PREFIX",
    "create_materialized_universe_configuration",
    "delete_materialized_universe",
    "get_materialized_universe",
    "list_materialized_universes",
    "materialized_universe_is_active",
    "materialized_universe_source_uid",
    "update_materialized_universe",
]
