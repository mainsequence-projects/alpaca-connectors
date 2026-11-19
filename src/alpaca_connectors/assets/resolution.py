"""Shared ms-markets asset resolution helpers.

In the storage-first ms-markets model, ``Asset`` carries only ``uid`` / ``unique_identifier`` /
``asset_type``. Alpaca's immutable UUID and current symbol live on the required project detail row;
FIGI metadata lives on a separate optional detail row. These helpers are reused by bars, holdings,
portfolios, and API catalog search.

Markets rows (``Asset``, ``AssetCategory`` memberships, ``OpenFigiDetails``) are read with batched
``msm.api`` row calls, which run on the Markets API under the remote backend (ms-markets ADR
0047); this project's details table is read with one compiled statement on its own runtime, and no
statement joins a Markets table.
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
    """Load an Asset UID set with batched Markets reads."""
    from alpaca_connectors.markets_api import assets_by_uid

    return assets_by_uid(asset_uids)


def assets_by_unique_identifiers(unique_identifiers: Sequence[str]) -> dict[str, Any]:
    """Load an Asset identifier set with batched Markets reads."""
    from alpaca_connectors.markets_api import assets_by_identifier

    return assets_by_identifier(unique_identifiers)


def _alpaca_details_rows(where: Any) -> list[dict[str, Any]]:
    """Rows of this project's ``AlpacaAssetDetailsTable`` matching ``where`` (one query)."""
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from alpaca_connectors.assets.alpaca_asset_details import AlpacaAssetDetailsTable
    from alpaca_connectors.runtime import start_markets_engine

    runtime = start_markets_engine()
    statement = select(
        AlpacaAssetDetailsTable.asset_uid,
        AlpacaAssetDetailsTable.alpaca_asset_id,
        AlpacaAssetDetailsTable.symbol,
        AlpacaAssetDetailsTable.name,
    ).where(where)
    operation = compile_markets_statement(statement, context=runtime.context, operation="select")
    return operation_result_rows(execute_markets_operation(operation, context=runtime.context))


def alpaca_details_by_identifier(unique_identifiers: Sequence[str]) -> dict[str, dict[str, Any]]:
    """This project's Alpaca details of each registered asset, by its canonical identifier.

    The identifier is ``ALPACA::<alpaca_asset_id>`` by construction (ADR 0004), so one read of
    this project's details table by ``alpaca_asset_id`` answers it, with no Markets call. A details
    row goes with its Markets asset (``ON DELETE CASCADE``), so a row found here is a registered
    asset. An identifier that is not an Alpaca one, or has no details row, is absent.
    """
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from alpaca_connectors.assets.alpaca_asset_details import (
        AlpacaAssetDetailsTable,
        build_alpaca_unique_identifier,
        parse_alpaca_unique_identifier,
    )
    from alpaca_connectors.markets_api import read_in_batches
    from alpaca_connectors.runtime import start_markets_engine

    alpaca_ids: list[uuid.UUID] = []
    for identifier in dict.fromkeys(str(value) for value in unique_identifiers):
        try:
            alpaca_ids.append(parse_alpaca_unique_identifier(identifier))
        except (TypeError, ValueError):
            continue
    if not alpaca_ids:
        return {}
    runtime = start_markets_engine()
    details = AlpacaAssetDetailsTable.__table__.c

    def read(batch: list[uuid.UUID]) -> list[dict[str, Any]]:
        statement = select(
            details.asset_uid,
            details.alpaca_asset_id,
            details.symbol,
            details.name,
            details.asset_class,
            details.tradable,
            details.fractionable,
        ).where(details.alpaca_asset_id.in_(batch))
        operation = compile_markets_statement(
            statement, context=runtime.context, operation="select"
        )
        return operation_result_rows(execute_markets_operation(operation, context=runtime.context))

    return {
        build_alpaca_unique_identifier(row["alpaca_asset_id"]): {
            **row,
            "asset_uid": str(uuid.UUID(str(row["asset_uid"]))),
            "alpaca_asset_id": str(uuid.UUID(str(row["alpaca_asset_id"]))),
        }
        for row in read_in_batches(read, alpaca_ids)
    }


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
    """Resolve Alpaca symbols and optional FIGIs for an Asset set.

    One batched Markets read for the Assets, one query of this project's details table and one
    batched Markets read for OpenFIGI details; every existing Asset gets an entry, ``(None, None)``
    when it has neither.
    """
    from alpaca_connectors.assets.alpaca_asset_details import AlpacaAssetDetailsTable
    from alpaca_connectors.markets_api import (
        assets_by_identifier,
        openfigi_details_by_asset_uid,
    )

    normalized_identifiers = list(
        dict.fromkeys(str(identifier) for identifier in unique_identifiers)
    )
    if not normalized_identifiers:
        return {}
    assets = assets_by_identifier(normalized_identifiers)
    if not assets:
        return {}
    asset_uids = [uuid.UUID(str(asset.uid)) for asset in assets.values()]
    symbols = {
        str(row["asset_uid"]): row.get("symbol")
        for row in _alpaca_details_rows(AlpacaAssetDetailsTable.asset_uid.in_(asset_uids))
    }
    figis = openfigi_details_by_asset_uid(str(uid) for uid in asset_uids)
    return {
        identifier: (
            symbols.get(str(asset.uid)),
            getattr(figis.get(str(asset.uid)), "figi", None),
        )
        for identifier, asset in assets.items()
    }


def assets_for_ticker(ticker: str) -> list[Any]:
    """Return the typed ``Asset`` rows that a provider ticker resolves to.

    The current provider symbol is read only from required ``AlpacaAssetDetails``. Symbol is a
    lookup attribute, never the canonical identity. May return zero, one, or several assets.
    """
    from alpaca_connectors.assets.alpaca_asset_details import AlpacaAssetDetailsTable
    from alpaca_connectors.markets_api import assets_by_uid

    normalized = (ticker or "").strip()
    if not normalized:
        return []
    rows = _alpaca_details_rows(AlpacaAssetDetailsTable.symbol == normalized.upper())
    assets = assets_by_uid(row["asset_uid"] for row in rows)
    return [assets[uid] for uid in sorted(assets)]


def unique_identifiers_for_ticker(ticker: str) -> list[str]:
    """Return canonical Alpaca identifiers whose required detail symbol matches ``ticker``."""
    return [asset.unique_identifier for asset in assets_for_ticker(ticker)]


__all__ = [
    "alpaca_details_by_identifier",
    "assets_by_uids",
    "assets_by_unique_identifiers",
    "assets_for_ticker",
    "get_asset_by_unique_identifier",
    "openfigi_details_for_asset_uid",
    "ticker_and_optional_figi",
    "ticker_and_optional_figi_by_unique_identifiers",
    "unique_identifiers_for_ticker",
]
