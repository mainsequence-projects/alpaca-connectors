"""Shared ms-markets asset resolution helpers.

In the storage-first ms-markets model, ``Asset`` carries only ``uid`` / ``unique_identifier`` /
``asset_type``. Alpaca's immutable UUID and current symbol live on the required project detail row;
FIGI metadata lives on a separate optional detail row. These helpers are reused by bars, universes,
holdings, portfolios, and API catalog search.

All callers must have an attached markets runtime (``src.runtime.start_markets_engine``) first.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any


def get_asset_by_unique_identifier(unique_identifier: str) -> Any | None:
    """Return the typed ``Asset`` row for a unique identifier, or ``None``."""
    from msm.api.assets import Asset

    return Asset.get_by_unique_identifier(unique_identifier)


def assets_by_uids(asset_uids: Sequence[Any]) -> dict[str, Any]:
    """Load an Asset UID set with one governed backend query."""
    from msm.api.assets import Asset
    from msm.api.base import operation_result_rows
    from msm.models import AssetTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from src.runtime import start_markets_engine

    normalized_uids = list(dict.fromkeys(uuid.UUID(str(asset_uid)) for asset_uid in asset_uids))
    if not normalized_uids:
        return {}
    runtime = start_markets_engine()
    operation = compile_markets_statement(
        select(AssetTable).where(AssetTable.uid.in_(normalized_uids)),
        context=runtime.context,
        operation="select",
        models=[AssetTable],
        access="read",
    )
    assets = [
        Asset.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]
    return {str(asset.uid): asset for asset in assets}


def assets_by_unique_identifiers(unique_identifiers: Sequence[str]) -> dict[str, Any]:
    """Load an Asset identifier set with one governed backend query."""
    from msm.api.assets import Asset
    from msm.api.base import operation_result_rows
    from msm.models import AssetTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from src.runtime import start_markets_engine

    normalized_identifiers = list(
        dict.fromkeys(str(identifier) for identifier in unique_identifiers)
    )
    if not normalized_identifiers:
        return {}
    runtime = start_markets_engine()
    operation = compile_markets_statement(
        select(AssetTable).where(AssetTable.unique_identifier.in_(normalized_identifiers)),
        context=runtime.context,
        operation="select",
        models=[AssetTable],
        access="read",
    )
    assets = [
        Asset.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]
    return {asset.unique_identifier: asset for asset in assets}


def openfigi_details_for_asset_uid(asset_uid: Any) -> Any | None:
    """Return the ``OpenFigiDetails`` row for an asset uid, or ``None``."""
    from msm.api.assets import OpenFigiDetails

    rows = OpenFigiDetails.filter(asset_uid=str(asset_uid), limit=1)
    return rows[0] if rows else None


def ticker_and_optional_figi(unique_identifier: str) -> tuple[str | None, str | None]:
    """Resolve the Alpaca symbol and optional FIGI for a canonical asset identifier."""
    return ticker_and_optional_figi_by_unique_identifiers([unique_identifier]).get(
        unique_identifier,
        (None, None),
    )


def ticker_and_optional_figi_by_unique_identifiers(
    unique_identifiers: Sequence[str],
) -> dict[str, tuple[str | None, str | None]]:
    """Resolve Alpaca symbols and optional FIGIs for an Asset set in one query."""
    from msm.api.base import operation_result_rows
    from msm.models import AssetTable, OpenFigiAssetDetailsTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from src.assets.alpaca_asset_details import AlpacaAssetDetailsTable
    from src.runtime import start_markets_engine

    normalized_identifiers = list(
        dict.fromkeys(str(identifier) for identifier in unique_identifiers)
    )
    if not normalized_identifiers:
        return {}
    runtime = start_markets_engine()
    statement = (
        select(
            AssetTable.unique_identifier,
            AlpacaAssetDetailsTable.symbol.label("ticker"),
            OpenFigiAssetDetailsTable.figi,
        )
        .select_from(AssetTable)
        .outerjoin(
            AlpacaAssetDetailsTable,
            AlpacaAssetDetailsTable.asset_uid == AssetTable.uid,
        )
        .outerjoin(
            OpenFigiAssetDetailsTable,
            OpenFigiAssetDetailsTable.asset_uid == AssetTable.uid,
        )
        .where(AssetTable.unique_identifier.in_(normalized_identifiers))
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
        models=[AssetTable, AlpacaAssetDetailsTable, OpenFigiAssetDetailsTable],
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(operation, context=runtime.context))
    return {str(row["unique_identifier"]): (row.get("ticker"), row.get("figi")) for row in rows}


def asset_unique_identifiers_for_category(category_unique_identifier: str) -> list[str]:
    """Return the asset unique identifiers that are members of an asset category.

    Raises ``ValueError`` if the category does not exist. Membership integrity is enforced by the
    migrated foreign keys, and the membership-to-Asset join is executed as one governed query.
    """
    from msm.api.assets import AssetCategory
    from msm.api.base import operation_result_rows
    from msm.models import AssetCategoryMembershipTable, AssetTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from src.runtime import start_markets_engine

    category = AssetCategory.get_by_unique_identifier(category_unique_identifier)
    if category is None:
        raise ValueError(
            f"Missing asset category for unique_identifier: {category_unique_identifier!r}"
        )

    runtime = start_markets_engine()
    statement = (
        select(AssetTable.uid, AssetTable.unique_identifier)
        .select_from(AssetCategoryMembershipTable)
        .join(AssetTable, AssetTable.uid == AssetCategoryMembershipTable.asset_uid)
        .where(AssetCategoryMembershipTable.category_uid == category.uid)
        .order_by(AssetTable.unique_identifier.asc())
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
        models=[AssetCategoryMembershipTable, AssetTable],
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(operation, context=runtime.context))
    return [str(row["unique_identifier"]) for row in rows]


def assets_for_ticker(ticker: str) -> list[Any]:
    """Return the typed ``Asset`` rows that a provider ticker resolves to.

    The current provider symbol is read only from required ``AlpacaAssetDetails``. Symbol is a
    lookup attribute, never the canonical identity. May return zero, one, or several assets.
    """
    from msm.api.assets import Asset
    from msm.api.base import operation_result_rows
    from msm.models import AssetTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from src.assets.alpaca_asset_details import AlpacaAssetDetailsTable
    from src.runtime import start_markets_engine

    normalized = (ticker or "").strip()
    if not normalized:
        return []

    runtime = start_markets_engine()
    statement = (
        select(AssetTable)
        .select_from(AlpacaAssetDetailsTable)
        .join(AssetTable, AssetTable.uid == AlpacaAssetDetailsTable.asset_uid)
        .where(AlpacaAssetDetailsTable.symbol == normalized.upper())
        .order_by(AssetTable.uid.asc())
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
        models=[AlpacaAssetDetailsTable, AssetTable],
        access="read",
    )
    return [
        Asset.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]


def unique_identifiers_for_ticker(ticker: str) -> list[str]:
    """Return canonical Alpaca identifiers whose required detail symbol matches ``ticker``."""
    return [asset.unique_identifier for asset in assets_for_ticker(ticker)]


__all__ = [
    "asset_unique_identifiers_for_category",
    "assets_by_uids",
    "assets_by_unique_identifiers",
    "assets_for_ticker",
    "get_asset_by_unique_identifier",
    "openfigi_details_for_asset_uid",
    "ticker_and_optional_figi",
    "ticker_and_optional_figi_by_unique_identifiers",
    "unique_identifiers_for_ticker",
]
