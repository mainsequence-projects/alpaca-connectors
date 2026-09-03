"""Shared ms-markets asset resolution helpers.

In the storage-first ms-markets model, ``Asset`` carries only ``uid`` / ``unique_identifier`` /
``asset_type``. Provider symbols (ticker, FIGI) live on ``OpenFigiDetails`` keyed by ``asset_uid``.
There is no bulk ``__in`` filter, so these helpers do per-item lookups (accepted N round-trips,
see migration doc R-2). They are reused by the bars DataNode, ETF holdings categories, CLI ticker
resolution, and the chart API asset search.

All callers must have an attached markets runtime (``src.runtime.start_markets_engine``) first.
"""

from __future__ import annotations

from typing import Any

_MEMBERSHIP_LIMIT = 10_000


def get_asset_by_unique_identifier(unique_identifier: str) -> Any | None:
    """Return the typed ``Asset`` row for a unique identifier, or ``None``."""
    from msm.api.assets import Asset

    return Asset.get_by_unique_identifier(unique_identifier)


def openfigi_details_for_asset_uid(asset_uid: Any) -> Any | None:
    """Return the ``OpenFigiDetails`` row for an asset uid, or ``None``."""
    from msm.api.assets import OpenFigiDetails

    rows = OpenFigiDetails.filter(asset_uid=str(asset_uid), limit=1)
    return rows[0] if rows else None


def ticker_figi_for_unique_identifier(unique_identifier: str) -> tuple[str | None, str | None]:
    """Resolve ``(ticker, figi)`` for an asset unique identifier.

    ``ticker`` comes from ``OpenFigiDetails`` (or ``None`` if there is no detail row). ``figi``
    falls back to the asset ``unique_identifier`` itself, which for public equities is the FIGI —
    matching the legacy ``asset.figi or asset.unique_identifier`` behavior.
    """
    asset = get_asset_by_unique_identifier(unique_identifier)
    if asset is None:
        return (None, unique_identifier)
    details = openfigi_details_for_asset_uid(asset.uid)
    ticker = details.ticker if details is not None else None
    figi = (details.figi if details is not None else None) or unique_identifier
    return (ticker, figi)


def asset_unique_identifiers_for_category(category_unique_identifier: str) -> list[str]:
    """Return the asset unique identifiers that are members of an asset category.

    Raises ``ValueError`` if the category does not exist or a membership references an asset that
    cannot be loaded — preserving the legacy strict resolution behavior.
    """
    from msm.api.assets import Asset, AssetCategory, AssetCategoryMembership

    category = AssetCategory.get_by_unique_identifier(category_unique_identifier)
    if category is None:
        raise ValueError(
            f"Missing asset category for unique_identifier: {category_unique_identifier!r}"
        )

    memberships = AssetCategoryMembership.filter(
        category_uid=str(category.uid), limit=_MEMBERSHIP_LIMIT
    )
    unique_identifiers: list[str] = []
    missing_asset_uids: list[str] = []
    for membership in memberships:
        asset = Asset.get_by_uid(membership.asset_uid)
        if asset is None:
            missing_asset_uids.append(str(membership.asset_uid))
            continue
        unique_identifiers.append(asset.unique_identifier)

    if missing_asset_uids:
        raise ValueError(
            f"Asset category contains asset uids that could not be loaded: {missing_asset_uids!r}"
        )
    return unique_identifiers


def assets_for_ticker(ticker: str) -> list[Any]:
    """Return the typed ``Asset`` rows that a provider ticker resolves to.

    Replaces the legacy ``Asset.filter(current_snapshot__ticker__in=...)`` lookup. Resolves through
    two paths so it is correct regardless of the registration convention (OQ-2 in the migration
    doc):

    1. ``OpenFigiDetails.ticker == ticker`` → ``asset_uid`` → ``Asset`` — the convention this
       project uses (equities are registered with the FIGI as ``unique_identifier`` and the ticker
       on the provider detail row).
    2. ``Asset.unique_identifier == ticker`` — in case an asset was registered (by another process)
       with the ticker itself as the canonical identifier. This is a no-op for FIGI-keyed assets
       (tickers and FIGIs never collide), so it adds robustness without false matches.

    May return zero, one, or several (ambiguous) assets; callers preserve their own
    missing/unique/ambiguous classification. De-duplicated by ``asset.uid``.
    """
    from msm.api.assets import Asset, OpenFigiDetails

    normalized = (ticker or "").strip()
    if not normalized:
        return []

    assets: list[Any] = []
    seen_uids: set[str] = set()

    def _add(asset: Any) -> None:
        if asset is None:
            return
        uid_key = str(asset.uid)
        if uid_key in seen_uids:
            return
        seen_uids.add(uid_key)
        assets.append(asset)

    for details in OpenFigiDetails.filter(ticker=normalized):
        _add(Asset.get_by_uid(details.asset_uid))

    _add(Asset.get_by_unique_identifier(normalized))

    return assets


def unique_identifiers_for_ticker(ticker: str) -> list[str]:
    """Return asset unique identifiers whose ``OpenFigiDetails.ticker`` matches ``ticker``."""
    return [asset.unique_identifier for asset in assets_for_ticker(ticker)]


__all__ = [
    "asset_unique_identifiers_for_category",
    "assets_for_ticker",
    "get_asset_by_unique_identifier",
    "openfigi_details_for_asset_uid",
    "ticker_figi_for_unique_identifier",
    "unique_identifiers_for_ticker",
]
