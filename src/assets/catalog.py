"""Read-only catalog operations for shared ms-markets Assets."""

from __future__ import annotations

from typing import Any


def serialize_asset(asset) -> dict[str, Any]:
    from src.assets.resolution import openfigi_details_for_asset_uid

    details = openfigi_details_for_asset_uid(asset.uid)
    return {
        **asset.model_dump(mode="json"),
        "uid": str(asset.uid),
        "ticker": getattr(details, "ticker", None),
        "name": getattr(details, "name", None),
        "figi": getattr(details, "figi", None),
    }


def get_asset(asset_uid: str) -> dict[str, Any] | None:
    from msm.api.assets import Asset

    from src.runtime import start_markets_engine

    start_markets_engine()
    asset = Asset.get_by_uid(asset_uid)
    return serialize_asset(asset) if asset else None


def list_assets(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    ordering: str = "ticker",
) -> tuple[list[dict[str, Any]], int]:
    from msm.api.base import operation_result_rows
    from msm.models import AssetTable, OpenFigiAssetDetailsTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    from src.runtime import start_markets_engine

    runtime = start_markets_engine()
    statement = (
        select(
            AssetTable.uid,
            AssetTable.unique_identifier,
            AssetTable.asset_type,
            OpenFigiAssetDetailsTable.ticker,
            OpenFigiAssetDetailsTable.name,
            OpenFigiAssetDetailsTable.figi,
        )
        .select_from(AssetTable)
        .outerjoin(
            OpenFigiAssetDetailsTable,
            OpenFigiAssetDetailsTable.asset_uid == AssetTable.uid,
        )
    )
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                AssetTable.unique_identifier.ilike(pattern),
                OpenFigiAssetDetailsTable.ticker.ilike(pattern),
                OpenFigiAssetDetailsTable.name.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(statement.subquery())
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "ticker": OpenFigiAssetDetailsTable.ticker,
        "unique_identifier": AssetTable.unique_identifier,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported asset ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    page_statement = statement.order_by(ordering_expression.nulls_last(), AssetTable.uid.asc())
    page_statement = page_statement.limit(limit).offset(offset)
    page_operation = compile_markets_statement(
        page_statement,
        context=runtime.context,
        operation="select",
        models=[AssetTable, OpenFigiAssetDetailsTable],
        access="read",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[AssetTable, OpenFigiAssetDetailsTable],
        access="read",
    )
    items = operation_result_rows(
        execute_markets_operation(page_operation, context=runtime.context)
    )
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    return items, total


__all__ = ["get_asset", "list_assets", "serialize_asset"]
