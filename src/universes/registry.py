"""Connector-owned AssetUniverse identity and relational links."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from msm.api.base import MarketsMetaTableRow, operation_result_rows
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from msm.models.assets.categories import AssetCategoryTable
from pydantic import ConfigDict
from sqlalchemy import Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin
from src.universes.sources import UniverseSourceTable

UTC = dt.timezone.utc


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class AssetUniverseTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """One registered universe linking its source to its materialized category."""

    __project_storage_concept__ = "asset_universe"
    __markets_base_identifier__ = "AssetUniverse"
    __metatable_description__ = (
        "Registered Alpaca connector asset universes. Each row owns lifecycle state and links one "
        "explicit UniverseSource to one ms-markets AssetCategory materialization target."
    )
    __table_args__ = markets_table_args(
        "AssetUniverse",
        Index(None, "source_uid", unique=True),
        Index(None, "asset_category_uid", unique=True),
        Index(None, "is_active"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={
            "label": "Universe UID",
            "description": "Stable UUID identity of this registered Asset Universe.",
        },
    )
    source_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{UniverseSourceTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Source UID",
            "description": (
                "Required UniverseSource UID containing the explicit symbol and extraction URL."
            ),
        },
    )
    asset_category_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AssetCategoryTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Asset Category UID",
            "description": (
                "Required ms-markets AssetCategory UID whose memberships materialize this universe."
            ),
        },
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        info={
            "label": "Active",
            "description": "Whether this registered universe may run and feed market-data jobs.",
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={
            "label": "Created At",
            "description": "UTC time when this registered universe was created.",
        },
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        info={
            "label": "Updated At",
            "description": "UTC time of the latest registered-universe lifecycle change.",
        },
    )


class AssetUniverse(MarketsMetaTableRow):
    """Typed row operations for ``AssetUniverseTable``."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AssetUniverseTable]] = AssetUniverseTable
    __required_tables__: ClassVar[list[type[Any]]] = [
        UniverseSourceTable,
        AssetCategoryTable,
        AssetUniverseTable,
    ]

    source_uid: uuid.UUID
    asset_category_uid: uuid.UUID
    is_active: bool
    created_at: dt.datetime
    updated_at: dt.datetime


def create_asset_universe(
    *,
    source_uid: uuid.UUID | str,
    asset_category_uid: uuid.UUID | str,
    is_active: bool = True,
    uid: uuid.UUID | str | None = None,
) -> AssetUniverse:
    """Create one registered Asset Universe with both required relationships."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    from src.runtime import start_markets_engine

    now = utc_now()
    values: dict[str, Any] = {
        "uid": uuid.UUID(str(uid)) if uid is not None else new_markets_uid(),
        "source_uid": uuid.UUID(str(source_uid)),
        "asset_category_uid": uuid.UUID(str(asset_category_uid)),
        "is_active": bool(is_active),
        "created_at": now,
        "updated_at": now,
    }
    start_markets_engine()
    runtime = resolve_runtime(
        models=[UniverseSourceTable, AssetCategoryTable, AssetUniverseTable],
        row_model_name="AssetUniverse",
    )
    result = create_model(runtime.context, model=AssetUniverseTable, values=values)
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Asset Universe create returned no row.")
    return AssetUniverse.model_validate(rows[0])


def get_asset_universe(universe_uid: uuid.UUID | str) -> AssetUniverse | None:
    from src.runtime import start_markets_engine

    start_markets_engine()
    return AssetUniverse.get_by_uid(universe_uid)


def get_asset_universe_by_source_uid(
    source_uid: uuid.UUID | str,
) -> AssetUniverse | None:
    from src.runtime import start_markets_engine

    start_markets_engine()
    rows = AssetUniverse.filter(source_uid=str(source_uid), limit=2)
    if len(rows) > 1:
        raise RuntimeError(f"Universe source {source_uid!s} is linked to multiple universes.")
    return rows[0] if rows else None


def get_asset_universe_by_category_uid(
    asset_category_uid: uuid.UUID | str,
) -> AssetUniverse | None:
    from src.runtime import start_markets_engine

    start_markets_engine()
    rows = AssetUniverse.filter(asset_category_uid=str(asset_category_uid), limit=2)
    if len(rows) > 1:
        raise RuntimeError(f"AssetCategory {asset_category_uid!s} is linked to multiple universes.")
    return rows[0] if rows else None


def update_asset_universe_state(
    universe_uid: uuid.UUID | str,
    *,
    is_active: bool,
) -> AssetUniverse:
    universe = get_asset_universe(universe_uid)
    if universe is None:
        raise LookupError(f"Asset Universe {universe_uid!s} does not exist.")
    return AssetUniverse.update(
        universe_uid,
        {"is_active": bool(is_active), "updated_at": utc_now()},
    )


def delete_asset_universe_row(universe_uid: uuid.UUID | str) -> dict[str, Any]:
    if get_asset_universe(universe_uid) is None:
        raise LookupError(f"Asset Universe {universe_uid!s} does not exist.")
    return AssetUniverse.delete(universe_uid)


def project_asset_universe_models() -> list[type[AssetUniverseTable]]:
    return [AssetUniverseTable]


__all__ = [
    "AssetUniverse",
    "AssetUniverseTable",
    "create_asset_universe",
    "delete_asset_universe_row",
    "get_asset_universe",
    "get_asset_universe_by_category_uid",
    "get_asset_universe_by_source_uid",
    "project_asset_universe_models",
    "update_asset_universe_state",
]
