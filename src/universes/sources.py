"""Durable user-maintained extraction sources for provider-derived universes."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from importlib.resources import files
from typing import Any, ClassVar
from urllib.parse import urlparse

from msm.api.base import MarketsMetaTableRow, operation_result_rows
from msm.base import MarketsBase, markets_table_args, new_markets_uid
from pydantic import ConfigDict
from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin

UTC = dt.timezone.utc


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class UniverseSourceTable(ProjectStorageNameMixin, AlpacaMarketsMetaTableMixin, MarketsBase):
    """One user-maintained URL that can be materialized as an AssetCategory universe."""

    __project_storage_concept__ = "universe_source"
    __markets_base_identifier__ = "UniverseSource"
    __metatable_description__ = (
        "User-maintained universe extraction sources. Each row identifies one provider URL and "
        "symbol that can be previewed or synchronized into a separate AssetCategory."
    )
    __table_args__ = markets_table_args(
        "UniverseSource",
        Index(None, "symbol"),
        Index(None, "enabled"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={"label": "UID", "description": "Stable UUID identity of this extraction source."},
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={"label": "Name", "description": "User-facing name of the extraction source."},
    )
    symbol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={
            "label": "Symbol",
            "description": "Normalized source symbol used to name the materialized universe.",
        },
    )
    source_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        info={
            "label": "Source URL",
            "description": "Provider URL passed to etfhextractor for holdings extraction.",
        },
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        info={
            "label": "Enabled",
            "description": "Whether this source is eligible for preview and synchronization.",
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Created At", "description": "UTC time when this source was created."},
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        info={"label": "Updated At", "description": "UTC time of the latest source change."},
    )


class UniverseSource(MarketsMetaTableRow):
    """Typed row operations for ``UniverseSourceTable``."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[UniverseSourceTable]] = UniverseSourceTable
    __required_tables__: ClassVar[list[type[UniverseSourceTable]]] = [UniverseSourceTable]

    name: str
    symbol: str
    source_url: str
    enabled: bool
    created_at: dt.datetime
    updated_at: dt.datetime


def normalize_source_values(
    *,
    name: str,
    symbol: str,
    source_url: str,
) -> dict[str, str]:
    normalized_name = (name or "").strip()
    normalized_symbol = (symbol or "").strip().upper()
    normalized_url = (source_url or "").strip()
    if not normalized_name:
        raise ValueError("Universe source name must not be empty.")
    if not normalized_symbol:
        raise ValueError("Universe source symbol must not be empty.")
    parsed_url = urlparse(normalized_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("Universe source URL must be an absolute HTTP or HTTPS URL.")
    return {
        "name": normalized_name,
        "symbol": normalized_symbol,
        "source_url": normalized_url,
    }


def create_universe_source(
    *,
    name: str,
    symbol: str,
    source_url: str,
    enabled: bool = True,
    uid: uuid.UUID | str | None = None,
) -> UniverseSource:
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    from src.runtime import start_markets_engine

    values: dict[str, Any] = {
        **normalize_source_values(name=name, symbol=symbol, source_url=source_url),
        "enabled": enabled,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    if uid is not None:
        values["uid"] = uuid.UUID(str(uid))
    start_markets_engine()
    runtime = resolve_runtime(models=[UniverseSourceTable], row_model_name="UniverseSource")
    result = create_model(runtime.context, model=UniverseSourceTable, values=values)
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Universe source create returned no row.")
    return UniverseSource.model_validate(rows[0])


def get_universe_source(source_uid: uuid.UUID | str) -> UniverseSource | None:
    from src.runtime import start_markets_engine

    start_markets_engine()
    return UniverseSource.get_by_uid(source_uid)


def list_universe_sources(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    enabled: bool | None = None,
    ordering: str = "name",
) -> tuple[list[UniverseSource], int]:
    from msm.api.base import operation_result_rows
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    from src.runtime import start_markets_engine

    start_markets_engine()
    runtime = resolve_runtime(models=[UniverseSourceTable], row_model_name="UniverseSource")
    statement = select(UniverseSourceTable)
    if enabled is not None:
        statement = statement.where(UniverseSourceTable.enabled == enabled)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                UniverseSourceTable.name.ilike(pattern),
                UniverseSourceTable.symbol.ilike(pattern),
                UniverseSourceTable.source_url.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(
        statement.order_by(None).subquery()
    )
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "name": UniverseSourceTable.name,
        "symbol": UniverseSourceTable.symbol,
        "updated_at": UniverseSourceTable.updated_at,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported universe-source ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    statement = statement.order_by(ordering_expression, UniverseSourceTable.uid.asc())
    page_operation = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
        models=[UniverseSourceTable],
        access="read",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[UniverseSourceTable],
        access="read",
    )
    rows = [
        UniverseSource.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(page_operation, context=runtime.context)
        )
    ]
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    return rows, total


def update_universe_source(
    source_uid: uuid.UUID | str,
    *,
    name: str | None = None,
    symbol: str | None = None,
    source_url: str | None = None,
    enabled: bool | None = None,
) -> UniverseSource:
    current = get_universe_source(source_uid)
    if current is None:
        raise LookupError(f"Universe source {source_uid!s} does not exist.")
    normalized = normalize_source_values(
        name=name if name is not None else current.name,
        symbol=symbol if symbol is not None else current.symbol,
        source_url=source_url if source_url is not None else current.source_url,
    )
    values: dict[str, Any] = {**normalized, "updated_at": utc_now()}
    if enabled is not None:
        values["enabled"] = enabled
    return UniverseSource.update(source_uid, values)


def delete_universe_source(source_uid: uuid.UUID | str) -> dict[str, Any]:
    if get_universe_source(source_uid) is None:
        raise LookupError(f"Universe source {source_uid!s} does not exist.")
    return UniverseSource.delete(source_uid)


def load_default_universe_sources() -> list[dict[str, Any]]:
    """Load packaged bootstrap data; normal universe operations never consult this file."""
    resource = files("src.universes").joinpath("fixtures/default_sources.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("Default universe-source data must contain at least one row.")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Every default universe-source entry must be an object.")
        normalized = normalize_source_values(
            name=str(item.get("name", "")),
            symbol=str(item.get("symbol", "")),
            source_url=str(item.get("source_url", "")),
        )
        rows.append(
            {
                "uid": uuid.UUID(str(item["uid"])),
                **normalized,
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return rows


def seed_default_universe_sources() -> list[UniverseSource]:
    """Idempotently reconcile packaged starter rows into the MetaTable.

    Seed identity is stable by UUID. When packaged seed metadata changes, the existing row is
    updated in place instead of leaving stale data or creating a duplicate.
    """
    seeded: list[UniverseSource] = []
    for source_values in load_default_universe_sources():
        existing = get_universe_source(source_values["uid"])
        if existing is None:
            seeded.append(create_universe_source(**source_values))
            continue
        if any(
            getattr(existing, field) != source_values[field]
            for field in ("name", "symbol", "source_url", "enabled")
        ):
            existing = update_universe_source(
                source_values["uid"],
                name=source_values["name"],
                symbol=source_values["symbol"],
                source_url=source_values["source_url"],
                enabled=source_values["enabled"],
            )
        seeded.append(existing)
    return seeded


def project_universe_models() -> list[type[UniverseSourceTable]]:
    return [UniverseSourceTable]


__all__ = [
    "UniverseSource",
    "UniverseSourceTable",
    "create_universe_source",
    "delete_universe_source",
    "get_universe_source",
    "list_universe_sources",
    "load_default_universe_sources",
    "normalize_source_values",
    "project_universe_models",
    "seed_default_universe_sources",
    "update_universe_source",
]
