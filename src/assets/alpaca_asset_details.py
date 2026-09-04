"""Project-owned Alpaca identity details for canonical ms-markets assets.

``AssetTable`` remains provider-neutral.  Every asset registered by this connector is keyed by
the immutable Alpaca asset UUID and has exactly one row in this table.  OpenFIGI details are a
separate, optional enrichment.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from msm.api.base import MarketsMetaTableRow, operation_result_rows
from msm.base import MarketsBase, markets_table_args
from msm.models import AssetTable
from pydantic import AliasChoices, ConfigDict, Field
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin

ALPACA_IDENTIFIER_PREFIX = "ALPACA::"

ASSET_TYPE_BY_ALPACA_CLASS = {
    "us_equity": "equity",
    "us_option": "option",
    "crypto": "crypto",
    "crypto_perp": "crypto_perpetual",
}


def normalize_alpaca_asset_id(value: uuid.UUID | str) -> uuid.UUID:
    """Return a validated Alpaca UUID."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value).strip())


def build_alpaca_unique_identifier(value: uuid.UUID | str) -> str:
    """Build the canonical provider-native ``Asset.unique_identifier``."""
    return f"{ALPACA_IDENTIFIER_PREFIX}{normalize_alpaca_asset_id(value)}"


def parse_alpaca_unique_identifier(value: str) -> uuid.UUID:
    """Parse a canonical Alpaca identifier, rejecting every other namespace."""
    if not value.startswith(ALPACA_IDENTIFIER_PREFIX):
        raise ValueError(f"Not an Alpaca asset identifier: {value!r}.")
    return normalize_alpaca_asset_id(value.removeprefix(ALPACA_IDENTIFIER_PREFIX))


def asset_type_from_alpaca_class(asset_class: str) -> str:
    """Map the Alpaca provider class to the small ms-markets asset taxonomy."""
    normalized = str(asset_class).strip().lower()
    try:
        return ASSET_TYPE_BY_ALPACA_CLASS[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(ASSET_TYPE_BY_ALPACA_CLASS))
        raise ValueError(
            f"Unsupported Alpaca asset_class {asset_class!r}. Supported values: {supported}."
        ) from exc


class AlpacaAssetDetailsTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """Required Alpaca provider details, one row per connector-owned Asset."""

    __project_storage_concept__ = "asset_alpaca"
    __markets_base_identifier__ = "AlpacaAssetDetails"
    __metatable_description__ = (
        "Required Alpaca provider identity and trading metadata for assets registered by the "
        "Alpaca connector. The primary key is also a foreign key to AssetTable.uid."
    )
    __table_args__ = markets_table_args(
        "AlpacaAssetDetails",
        Index(None, "alpaca_asset_id", unique=True),
        Index(None, "symbol"),
        Index(None, "asset_class", "symbol"),
        Index(None, "exchange"),
    )

    asset_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AssetTable.__table__.fullname}.uid", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        info={
            "label": "Asset UID",
            "description": "Canonical Main Sequence AssetTable.uid for this detail row.",
        },
    )
    alpaca_asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        info={
            "label": "Alpaca Asset ID",
            "description": "Immutable UUID assigned by Alpaca to this asset.",
        },
    )
    symbol: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        info={"label": "Symbol", "description": "Current Alpaca trading symbol."},
    )
    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        info={"label": "Name", "description": "Current Alpaca asset name."},
    )
    asset_class: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={"label": "Asset Class", "description": "Alpaca asset class."},
    )
    exchange: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        info={"label": "Exchange", "description": "Alpaca exchange code."},
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={"label": "Status", "description": "Current Alpaca asset status."},
    )
    tradable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    marginable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    shortable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    easy_to_borrow: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fractionable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    attributes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    refreshed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlpacaAssetDetails(MarketsMetaTableRow):
    """Typed row API for ``AlpacaAssetDetailsTable``."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AlpacaAssetDetailsTable]] = AlpacaAssetDetailsTable
    __required_tables__: ClassVar[list[type[Any]]] = [
        AssetTable,
        AlpacaAssetDetailsTable,
    ]
    __upsert_keys__: ClassVar[tuple[str, ...]] = ("asset_uid",)

    uid: uuid.UUID = Field(validation_alias=AliasChoices("uid", "asset_uid"))
    asset_uid: uuid.UUID
    alpaca_asset_id: uuid.UUID
    symbol: str
    name: str | None
    asset_class: str
    exchange: str | None
    status: str
    tradable: bool
    marginable: bool | None
    shortable: bool | None
    easy_to_borrow: bool | None
    fractionable: bool | None
    attributes: list[str]
    raw_payload: dict[str, Any] | None
    refreshed_at: dt.datetime


def alpaca_details_for_asset_uid(asset_uid: uuid.UUID | str) -> AlpacaAssetDetails | None:
    """Return the required Alpaca detail row for one Asset UID."""
    rows = AlpacaAssetDetails.filter(asset_uid=str(asset_uid), limit=1)
    return rows[0] if rows else None


def alpaca_details_for_asset_id(alpaca_asset_id: uuid.UUID | str) -> AlpacaAssetDetails | None:
    """Return the Alpaca detail row for one immutable provider UUID."""
    rows = AlpacaAssetDetails.filter(
        alpaca_asset_id=str(normalize_alpaca_asset_id(alpaca_asset_id)),
        limit=1,
    )
    return rows[0] if rows else None


def upsert_alpaca_asset_details(
    *,
    asset_uid: uuid.UUID | str,
    values: dict[str, Any],
) -> AlpacaAssetDetails:
    """Create or refresh the required Alpaca sidecar for an Asset."""
    from msm.api.assets import Asset
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import upsert_model

    alpaca_asset_id = normalize_alpaca_asset_id(values["alpaca_asset_id"])
    asset = Asset.get_by_uid(asset_uid)
    if asset is None:
        raise LookupError(f"Main Sequence Asset {asset_uid!s} does not exist.")
    expected_identifier = build_alpaca_unique_identifier(alpaca_asset_id)
    if asset.unique_identifier != expected_identifier:
        raise ValueError(
            "Alpaca asset detail identity does not match its parent Asset: "
            f"expected {expected_identifier!r}, got {asset.unique_identifier!r}."
        )

    runtime = resolve_runtime(
        models=[AssetTable, AlpacaAssetDetailsTable],
        row_model_name="AlpacaAssetDetails",
    )
    result = upsert_model(
        runtime.context,
        model=AlpacaAssetDetailsTable,
        values={"asset_uid": asset_uid, **values, "alpaca_asset_id": alpaca_asset_id},
        conflict_columns=("asset_uid",),
    )
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Alpaca asset detail upsert returned no row.")
    return AlpacaAssetDetails.model_validate(rows[0])


def project_asset_models() -> list[type]:
    """Project-owned asset MetaTables for migrations and runtime attachment."""
    return [AlpacaAssetDetailsTable]


__all__ = [
    "ALPACA_IDENTIFIER_PREFIX",
    "AlpacaAssetDetails",
    "AlpacaAssetDetailsTable",
    "alpaca_details_for_asset_id",
    "alpaca_details_for_asset_uid",
    "asset_type_from_alpaca_class",
    "build_alpaca_unique_identifier",
    "normalize_alpaca_asset_id",
    "parse_alpaca_unique_identifier",
    "project_asset_models",
    "upsert_alpaca_asset_details",
]
