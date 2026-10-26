"""Read-only catalog operations for the Alpaca-registered ms-markets Assets."""

from __future__ import annotations

import uuid
from typing import Any


def serialize_asset(asset) -> dict[str, Any]:
    from alpaca_connectors.assets.alpaca_asset_details import alpaca_details_for_asset_uid
    from alpaca_connectors.assets.resolution import openfigi_details_for_asset_uid

    alpaca_details = alpaca_details_for_asset_uid(asset.uid)
    figi_details = openfigi_details_for_asset_uid(asset.uid)
    if alpaca_details is None:
        raise LookupError(f"Asset {asset.uid!s} is missing required Alpaca asset details.")
    return {
        **asset.model_dump(mode="json"),
        "uid": str(asset.uid),
        "alpaca_asset_id": str(alpaca_details.alpaca_asset_id),
        "ticker": alpaca_details.symbol,
        "name": alpaca_details.name,
        "exchange": alpaca_details.exchange,
        "status": alpaca_details.status,
        "tradable": alpaca_details.tradable,
        "figi": getattr(figi_details, "figi", None),
        "composite_figi": getattr(figi_details, "composite", None),
    }


def get_asset(asset_uid: str) -> dict[str, Any] | None:
    from msm.api.assets import Asset

    from alpaca_connectors.runtime import start_markets_engine

    start_markets_engine()
    asset = Asset.get_by_uid(asset_uid)
    return serialize_asset(asset) if asset else None


def list_assets(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    ordering: str = "ticker",
    category_uid: uuid.UUID | str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Page this project's Alpaca asset details and add each page's Markets fields.

    The page and the count are compiled statements on ``AlpacaAssetDetailsTable`` alone; the
    canonical identifier is ``ALPACA::<alpaca_asset_id>`` by construction (ADR 0004). A category
    filter reads the category's memberships from Markets first, and the page's Assets and
    OpenFIGI details come from Markets in one batched read each (ms-markets ADR 0047).
    """
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import String, cast, func, literal, or_, select

    from alpaca_connectors.assets.alpaca_asset_details import (
        ALPACA_IDENTIFIER_PREFIX,
        AlpacaAssetDetailsTable,
    )
    from alpaca_connectors.markets_api import (
        assets_by_uid,
        category_member_asset_uids,
        openfigi_details_by_asset_uid,
    )
    from alpaca_connectors.runtime import start_markets_engine

    runtime = start_markets_engine()
    details = AlpacaAssetDetailsTable
    unique_identifier = func.concat(
        literal(ALPACA_IDENTIFIER_PREFIX), cast(details.alpaca_asset_id, String)
    )
    statement = select(
        details.asset_uid,
        details.alpaca_asset_id,
        details.symbol.label("ticker"),
        details.name,
        details.exchange,
        details.status,
        details.tradable,
    )
    if category_uid is not None:
        member_uids = [uuid.UUID(value) for value in category_member_asset_uids(category_uid)]
        if not member_uids:
            return [], 0
        statement = statement.where(details.asset_uid.in_(member_uids))
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                unique_identifier.ilike(pattern),
                details.symbol.ilike(pattern),
                details.name.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(statement.subquery())
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "ticker": details.symbol,
        "alpaca_asset_id": details.alpaca_asset_id,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported asset ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    page_statement = statement.order_by(ordering_expression.nulls_last(), details.asset_uid.asc())
    page_statement = page_statement.limit(limit).offset(offset)
    page_operation = compile_markets_statement(
        page_statement,
        context=runtime.context,
        operation="select",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
    )
    rows = operation_result_rows(execute_markets_operation(page_operation, context=runtime.context))
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    page_uids = [str(row["asset_uid"]) for row in rows]
    assets = assets_by_uid(page_uids)
    figis = openfigi_details_by_asset_uid(page_uids)
    items = []
    for row in rows:
        asset_uid = str(row["asset_uid"])
        asset = assets.get(asset_uid)
        figi = figis.get(asset_uid)
        values = {key: value for key, value in row.items() if key != "asset_uid"}
        items.append(
            {
                "uid": row["asset_uid"],
                "unique_identifier": (
                    asset.unique_identifier
                    if asset is not None
                    else f"{ALPACA_IDENTIFIER_PREFIX}{row['alpaca_asset_id']}"
                ),
                "asset_type": getattr(asset, "asset_type", None),
                **values,
                "figi": getattr(figi, "figi", None),
                "composite_figi": getattr(figi, "composite", None),
            }
        )
    return items, total


__all__ = ["get_asset", "list_assets", "serialize_asset"]
