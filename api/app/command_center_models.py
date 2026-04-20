from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TableFieldResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str = Field(..., description="Column key.")
    label: str | None = Field(default=None, description="Human-readable column label.")
    type: str | None = Field(default=None, description="Normalized field type.")
    nullable: bool | None = Field(default=None, description="Whether the field may be null.")
    nativeType: str | None = Field(default=None, description="Optional backend-native type.")
    provenance: str | None = Field(default=None, description="Field schema provenance.")
    reason: str | None = Field(default=None, description="Optional schema explanation.")
    derivedFrom: list[str] | None = Field(
        default=None,
        description="Optional source columns used to derive this field.",
    )
    warnings: list[str] | None = Field(
        default=None,
        description="Optional non-fatal field warnings.",
    )


class SourceMetadataResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str = Field(..., description="Source descriptor kind.")
    id: str | int | None = Field(default=None, description="Optional source identifier.")
    label: str | None = Field(default=None, description="Optional source label.")
    updatedAtMs: dt.datetime | int | str | None = Field(
        default=None,
        description="Optional source freshness timestamp.",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description="Optional source-specific metadata.",
    )


class DataNodeTableSourceInputResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["idle", "loading", "ready", "error"] = Field(
        ...,
        description="Loading state for the tabular source payload.",
    )
    error: str | None = Field(default=None, description="Optional source error.")
    columns: list[str] = Field(default_factory=list, description="Ordered column keys.")
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="JSON-compatible source rows keyed by column name.",
    )
    fields: list[TableFieldResponse] | None = Field(
        default=None,
        description="Optional normalized field schema.",
    )
    source: SourceMetadataResponse | None = Field(
        default=None,
        description="Optional source metadata.",
    )
    dataNodeId: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1)
    rangeStartMs: dt.datetime | int | str | None = Field(default=None)
    rangeEndMs: dt.datetime | int | str | None = Field(default=None)
    uniqueIdentifierList: list[str] | None = Field(default=None)
    updatedAtMs: dt.datetime | int | str | None = Field(default=None)
