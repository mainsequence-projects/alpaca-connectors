"""Durable user-maintained Alpaca stock-bar update configurations.

The configuration MetaTable is the application control plane.  It records which registered
Alpaca account supplies credentials, which asset source should be resolved at execution time, and
which already-migrated bar profile receives the observations.  Output MetaTable UIDs are never
stored here: the ``(frequency_id, feed, adjustment)`` triple deterministically selects storage.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar, Literal

from msm.api.base import MarketsMetaTableRow, operation_result_rows
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from msm.models import AccountTable
from msm.models.assets.core import AssetTable
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, delete
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin
from src.universes.registry import AssetUniverseTable

AssetSource = Literal["assets", "universe", "account_holdings"]
ASSET_SOURCES: tuple[AssetSource, ...] = ("assets", "universe", "account_holdings")
UTC = dt.timezone.utc
_UNSET = object()


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class AlpacaBarsConfigurationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """One reusable stock-bar update configuration."""

    __project_storage_concept__ = "bars_configuration"
    __markets_base_identifier__ = "AlpacaBarsConfiguration"
    __metatable_description__ = (
        "Reusable Alpaca stock-bar configurations. Each row selects a registered Alpaca account, "
        "one dynamic asset-source strategy, and one migrated frequency/feed/adjustment profile."
    )
    __table_args__ = markets_table_args(
        "AlpacaBarsConfiguration",
        CheckConstraint(
            "asset_source IN ('assets', 'universe', 'account_holdings')",
            name="ck_alpaca_bars_configuration_asset_source",
        ),
        CheckConstraint(
            "(asset_source = 'universe' AND universe_uid IS NOT NULL) OR "
            "(asset_source <> 'universe' AND universe_uid IS NULL)",
            name="ck_alpaca_bars_configuration_universe_scope",
        ),
        Index(None, "name"),
        Index(None, "enabled"),
        Index(None, "account_uid"),
        Index(None, "asset_source"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={
            "label": "Configuration UID",
            "description": "Stable UUID used to review and execute this stored configuration.",
        },
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={"label": "Name", "description": "User-facing configuration name."},
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        info={"label": "Description", "description": "Optional operator-facing description."},
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        info={
            "label": "Enabled",
            "description": "Disabled configurations remain reviewable but cannot run.",
        },
    )
    account_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AccountTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        nullable=False,
        info={
            "label": "Account UID",
            "description": "Registered Alpaca Account UID whose Secret names supply credentials.",
        },
    )
    asset_source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={
            "label": "Asset Source",
            "description": "Asset resolver: assets, universe, or account_holdings.",
        },
    )
    universe_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AssetUniverseTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        nullable=True,
        info={
            "label": "Universe UID",
            "description": "Registered AssetUniverse UID when the asset source is universe.",
        },
    )
    frequency_id: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={"label": "Frequency", "description": "Normalized Alpaca bar frequency."},
    )
    feed: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={"label": "Feed", "description": "Normalized Alpaca market-data feed."},
    )
    adjustment: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={"label": "Adjustment", "description": "Normalized Alpaca adjustment mode."},
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Created At", "description": "UTC creation time."},
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        info={"label": "Updated At", "description": "UTC time of the latest change."},
    )


class AlpacaBarsConfigurationAssetTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    MarketsBase,
):
    """Explicit Asset membership for configurations whose source is ``assets``."""

    __project_storage_concept__ = "bars_configuration_asset"
    __markets_base_identifier__ = "AlpacaBarsConfigurationAsset"
    __metatable_description__ = (
        "Explicit Asset membership for Alpaca bar configurations. Rows exist only for "
        "configurations whose asset_source is assets."
    )
    __table_args__ = markets_table_args(
        "AlpacaBarsConfigurationAsset",
        Index(None, "asset_uid"),
    )

    configuration_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AlpacaBarsConfigurationTable.__table__.fullname}.uid", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        info={
            "label": "Configuration UID",
            "description": "Parent AlpacaBarsConfiguration UID.",
        },
    )
    asset_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AssetTable.__table__.fullname}.uid", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
        info={"label": "Asset UID", "description": "Explicit registered Asset UID."},
    )


class AlpacaBarsConfiguration(MarketsMetaTableRow):
    """Typed configuration row enriched with explicit Asset memberships."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AlpacaBarsConfigurationTable]] = AlpacaBarsConfigurationTable
    __required_tables__: ClassVar[list[type[MarketsBase]]] = [
        AlpacaBarsConfigurationTable,
        AlpacaBarsConfigurationAssetTable,
    ]

    name: str
    description: str | None
    enabled: bool
    account_uid: uuid.UUID
    asset_source: AssetSource
    universe_uid: uuid.UUID | None
    frequency_id: str
    feed: str
    adjustment: str
    created_at: dt.datetime
    updated_at: dt.datetime
    asset_uids: list[uuid.UUID] = Field(default_factory=list)


def _normalize_name(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError("Bar configuration name must not be empty.")
    if len(normalized) > 255:
        raise ValueError("Bar configuration name must be at most 255 characters.")
    return normalized


def _normalize_description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def validate_configuration_scope(
    *,
    asset_source: str,
    asset_uids: list[uuid.UUID | str] | None,
    universe_uid: uuid.UUID | str | None,
) -> tuple[AssetSource, list[uuid.UUID], uuid.UUID | None]:
    """Normalize and validate the three mutually exclusive source shapes."""
    normalized_source = asset_source.strip().lower()
    if normalized_source not in ASSET_SOURCES:
        raise ValueError(
            f"Unsupported asset_source {asset_source!r}. Expected one of {list(ASSET_SOURCES)!r}."
        )
    normalized_asset_uids = list(
        dict.fromkeys(uuid.UUID(str(asset_uid)) for asset_uid in (asset_uids or []))
    )
    normalized_universe_uid = uuid.UUID(str(universe_uid)) if universe_uid is not None else None
    if normalized_source == "assets":
        if not normalized_asset_uids or normalized_universe_uid is not None:
            raise ValueError("assets source requires asset_uids and forbids universe_uid.")
    elif normalized_source == "universe":
        if normalized_universe_uid is None or normalized_asset_uids:
            raise ValueError("universe source requires universe_uid and forbids asset_uids.")
    elif normalized_asset_uids or normalized_universe_uid is not None:
        raise ValueError("account_holdings source forbids asset_uids and universe_uid.")
    return normalized_source, normalized_asset_uids, normalized_universe_uid  # type: ignore[return-value]


def _validate_references(
    *,
    account_uid: uuid.UUID,
    asset_source: AssetSource,
    asset_uids: list[uuid.UUID],
    universe_uid: uuid.UUID | None,
) -> None:
    from src.account.services import get_account_registration
    from src.assets.resolution import assets_by_uids
    from src.universes import get_asset_universe

    if get_account_registration(account_uid) is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    existing_assets = assets_by_uids(asset_uids)
    missing_assets = [
        str(asset_uid) for asset_uid in asset_uids if str(asset_uid) not in existing_assets
    ]
    if missing_assets:
        raise LookupError(f"These Asset UIDs do not exist: {sorted(missing_assets)!r}")
    if asset_source == "universe":
        universe = get_asset_universe(universe_uid)
        if universe is None:
            raise LookupError(f"Asset Universe {universe_uid!s} does not exist.")
        if not universe.is_active:
            raise ValueError(
                f"Asset Universe {universe_uid!s} is inactive and cannot be configured."
            )


def bar_configurations_for_universe(
    universe_uid: uuid.UUID | str,
) -> list[AlpacaBarsConfiguration]:
    """Return stored bar configurations that hold a real FK to one Asset Universe."""
    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    return sorted(
        AlpacaBarsConfiguration.filter(universe_uid=str(universe_uid), limit=10_000),
        key=lambda configuration: (configuration.name, str(configuration.uid)),
    )


def _configuration_asset_uids(configuration_uid: uuid.UUID | str) -> list[uuid.UUID]:
    return _configuration_asset_uids_by_configuration([configuration_uid]).get(
        str(configuration_uid),
        [],
    )


def _configuration_asset_uids_by_configuration(
    configuration_uids: list[uuid.UUID | str],
) -> dict[str, list[uuid.UUID]]:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    normalized_configuration_uids = list(
        dict.fromkeys(uuid.UUID(str(configuration_uid)) for configuration_uid in configuration_uids)
    )
    if not normalized_configuration_uids:
        return {}
    runtime = resolve_runtime(
        models=[AlpacaBarsConfigurationAssetTable],
        row_model_name="AlpacaBarsConfigurationAsset",
    )
    statement = (
        select(
            AlpacaBarsConfigurationAssetTable.configuration_uid,
            AlpacaBarsConfigurationAssetTable.asset_uid,
        )
        .where(
            AlpacaBarsConfigurationAssetTable.configuration_uid.in_(normalized_configuration_uids)
        )
        .order_by(
            AlpacaBarsConfigurationAssetTable.configuration_uid.asc(),
            AlpacaBarsConfigurationAssetTable.asset_uid.asc(),
        )
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
        models=[AlpacaBarsConfigurationAssetTable],
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(operation, context=runtime.context))
    memberships = {
        str(configuration_uid): [] for configuration_uid in normalized_configuration_uids
    }
    for row in rows:
        memberships[str(row["configuration_uid"])].append(uuid.UUID(str(row["asset_uid"])))
    return memberships


def _with_memberships(
    row: AlpacaBarsConfiguration,
    *,
    asset_uids: list[uuid.UUID] | None = None,
) -> AlpacaBarsConfiguration:
    return row.model_copy(
        update={
            "asset_uids": (_configuration_asset_uids(row.uid) if asset_uids is None else asset_uids)
        }
    )


def _replace_memberships(
    configuration_uid: uuid.UUID | str,
    asset_uids: list[uuid.UUID],
) -> None:
    from msm.api.base import operation_result_rows
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from msm.repositories.crud import bulk_upsert_model

    normalized_configuration_uid = uuid.UUID(str(configuration_uid))
    normalized_asset_uids = list(dict.fromkeys(asset_uids))
    runtime = resolve_runtime(
        models=[AlpacaBarsConfigurationAssetTable],
        row_model_name="AlpacaBarsConfigurationAsset",
    )
    if normalized_asset_uids:
        upsert_result = bulk_upsert_model(
            runtime.context,
            model=AlpacaBarsConfigurationAssetTable,
            values=[
                {
                    "configuration_uid": normalized_configuration_uid,
                    "asset_uid": asset_uid,
                }
                for asset_uid in normalized_asset_uids
            ],
            conflict_columns=("configuration_uid", "asset_uid"),
        )
        returned_asset_uids = {
            uuid.UUID(str(row["asset_uid"]))
            for row in operation_result_rows(upsert_result)
            if row.get("asset_uid") is not None
        }
        missing_asset_uids = set(normalized_asset_uids) - returned_asset_uids
        if missing_asset_uids:
            missing = ", ".join(str(asset_uid) for asset_uid in sorted(missing_asset_uids))
            raise RuntimeError(
                "Bulk bar-configuration membership upsert returned an incomplete result for: "
                f"{missing}. Existing memberships were not removed."
            )

    stale_memberships = delete(AlpacaBarsConfigurationAssetTable).where(
        AlpacaBarsConfigurationAssetTable.configuration_uid == normalized_configuration_uid
    )
    if normalized_asset_uids:
        stale_memberships = stale_memberships.where(
            AlpacaBarsConfigurationAssetTable.asset_uid.notin_(normalized_asset_uids)
        )
    delete_operation = compile_markets_statement(
        stale_memberships,
        context=runtime.context,
        operation="delete",
        models=[AlpacaBarsConfigurationAssetTable],
        access="write",
    )
    execute_markets_operation(delete_operation, context=runtime.context)


def create_bar_configuration(
    *,
    name: str,
    account_uid: uuid.UUID | str,
    asset_source: str,
    frequency_id: str,
    feed: str,
    adjustment: str,
    asset_uids: list[uuid.UUID | str] | None = None,
    universe_uid: uuid.UUID | str | None = None,
    description: str | None = None,
    enabled: bool = True,
    uid: uuid.UUID | str | None = None,
) -> AlpacaBarsConfiguration:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    from src.market_data.alpaca_bars_support import (
        normalize_adjustment,
        normalize_feed,
        normalize_frequency_id,
    )
    from src.market_data.storage import storage_for
    from src.runtime import account_runtime_models, start_markets_engine

    source, normalized_asset_uids, normalized_universe_uid = validate_configuration_scope(
        asset_source=asset_source,
        asset_uids=asset_uids,
        universe_uid=universe_uid,
    )
    normalized_account_uid = uuid.UUID(str(account_uid))
    normalized_frequency = normalize_frequency_id(frequency_id)
    normalized_feed = normalize_feed(feed)
    normalized_adjustment = normalize_adjustment(adjustment)
    storage_for(normalized_frequency, normalized_feed, normalized_adjustment)
    start_markets_engine(models=account_runtime_models())
    _validate_references(
        account_uid=normalized_account_uid,
        asset_source=source,
        asset_uids=normalized_asset_uids,
        universe_uid=normalized_universe_uid,
    )
    now = utc_now()
    values: dict[str, Any] = {
        "uid": uuid.UUID(str(uid)) if uid is not None else new_markets_uid(),
        "name": _normalize_name(name),
        "description": _normalize_description(description),
        "enabled": bool(enabled),
        "account_uid": normalized_account_uid,
        "asset_source": source,
        "universe_uid": normalized_universe_uid,
        "frequency_id": normalized_frequency,
        "feed": normalized_feed,
        "adjustment": normalized_adjustment,
        "created_at": now,
        "updated_at": now,
    }
    runtime = resolve_runtime(
        models=[AlpacaBarsConfigurationTable, AlpacaBarsConfigurationAssetTable],
        row_model_name="AlpacaBarsConfiguration",
    )
    result = create_model(runtime.context, model=AlpacaBarsConfigurationTable, values=values)
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Bar configuration create returned no row.")
    row = AlpacaBarsConfiguration.model_validate(rows[0])
    _replace_memberships(row.uid, normalized_asset_uids)
    return _with_memberships(row)


def get_bar_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaBarsConfiguration | None:
    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    row = AlpacaBarsConfiguration.get_by_uid(configuration_uid)
    return _with_memberships(row) if row is not None else None


def list_bar_configurations(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    enabled: bool | None = None,
    asset_source: str | None = None,
    ordering: str = "name",
) -> tuple[list[AlpacaBarsConfiguration], int]:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    runtime = resolve_runtime(
        models=[AlpacaBarsConfigurationTable, AlpacaBarsConfigurationAssetTable],
        row_model_name="AlpacaBarsConfiguration",
    )
    statement = select(AlpacaBarsConfigurationTable)
    if enabled is not None:
        statement = statement.where(AlpacaBarsConfigurationTable.enabled == enabled)
    if asset_source is not None:
        normalized_source = asset_source.strip().lower()
        if normalized_source not in ASSET_SOURCES:
            raise ValueError(f"Unsupported asset_source filter {asset_source!r}.")
        statement = statement.where(AlpacaBarsConfigurationTable.asset_source == normalized_source)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                AlpacaBarsConfigurationTable.name.ilike(pattern),
                AlpacaBarsConfigurationTable.description.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(
        statement.order_by(None).subquery()
    )
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "name": AlpacaBarsConfigurationTable.name,
        "updated_at": AlpacaBarsConfigurationTable.updated_at,
        "frequency_id": AlpacaBarsConfigurationTable.frequency_id,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported bar-configuration ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    statement = statement.order_by(ordering_expression, AlpacaBarsConfigurationTable.uid.asc())
    page_operation = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
        models=[AlpacaBarsConfigurationTable],
        access="read",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[AlpacaBarsConfigurationTable],
        access="read",
    )
    rows = [
        AlpacaBarsConfiguration.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(page_operation, context=runtime.context)
        )
    ]
    memberships = _configuration_asset_uids_by_configuration([row.uid for row in rows])
    rows = [_with_memberships(row, asset_uids=memberships.get(str(row.uid), [])) for row in rows]
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    return rows, total


def update_bar_configuration(
    configuration_uid: uuid.UUID | str,
    *,
    name: str | None = None,
    description: str | None | object = _UNSET,
    enabled: bool | None = None,
    account_uid: uuid.UUID | str | None = None,
    asset_source: str | None = None,
    asset_uids: list[uuid.UUID | str] | None | object = _UNSET,
    universe_uid: uuid.UUID | str | None | object = _UNSET,
    frequency_id: str | None = None,
    feed: str | None = None,
    adjustment: str | None = None,
) -> AlpacaBarsConfiguration:
    from src.market_data.alpaca_bars_support import (
        normalize_adjustment,
        normalize_feed,
        normalize_frequency_id,
    )
    from src.market_data.storage import storage_for

    current = get_bar_configuration(configuration_uid)
    if current is None:
        raise LookupError(f"Bar configuration {configuration_uid!s} does not exist.")
    requested_source = asset_source if asset_source is not None else current.asset_source
    if asset_source is None and asset_uids is _UNSET and universe_uid is _UNSET:
        requested_asset_uids = list(current.asset_uids)
        requested_universe_uid = current.universe_uid
    else:
        requested_asset_uids = None if asset_uids is _UNSET else asset_uids
        requested_universe_uid = None if universe_uid is _UNSET else universe_uid
    source, normalized_asset_uids, normalized_universe_uid = validate_configuration_scope(
        asset_source=requested_source,
        asset_uids=requested_asset_uids,
        universe_uid=requested_universe_uid,
    )
    normalized_account_uid = uuid.UUID(str(account_uid or current.account_uid))
    normalized_frequency = normalize_frequency_id(frequency_id or current.frequency_id)
    normalized_feed = normalize_feed(feed or current.feed)
    normalized_adjustment = normalize_adjustment(adjustment or current.adjustment)
    storage_for(normalized_frequency, normalized_feed, normalized_adjustment)
    _validate_references(
        account_uid=normalized_account_uid,
        asset_source=source,
        asset_uids=normalized_asset_uids,
        universe_uid=normalized_universe_uid,
    )
    values: dict[str, Any] = {
        "account_uid": normalized_account_uid,
        "asset_source": source,
        "universe_uid": normalized_universe_uid,
        "frequency_id": normalized_frequency,
        "feed": normalized_feed,
        "adjustment": normalized_adjustment,
        "updated_at": utc_now(),
    }
    if name is not None:
        values["name"] = _normalize_name(name)
    if description is not _UNSET:
        values["description"] = _normalize_description(
            description if isinstance(description, str) else None
        )
    if enabled is not None:
        values["enabled"] = bool(enabled)
    row = AlpacaBarsConfiguration.update(configuration_uid, values)
    _replace_memberships(configuration_uid, normalized_asset_uids)
    return _with_memberships(row)


def delete_bar_configuration(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    if get_bar_configuration(configuration_uid) is None:
        raise LookupError(f"Bar configuration {configuration_uid!s} does not exist.")
    return AlpacaBarsConfiguration.delete(configuration_uid)


def project_configuration_models() -> list[type[MarketsBase]]:
    return [AlpacaBarsConfigurationTable, AlpacaBarsConfigurationAssetTable]


__all__ = [
    "ASSET_SOURCES",
    "AlpacaBarsConfiguration",
    "AlpacaBarsConfigurationAssetTable",
    "AlpacaBarsConfigurationTable",
    "AssetSource",
    "create_bar_configuration",
    "bar_configurations_for_universe",
    "delete_bar_configuration",
    "get_bar_configuration",
    "list_bar_configurations",
    "project_configuration_models",
    "update_bar_configuration",
    "validate_configuration_scope",
]
