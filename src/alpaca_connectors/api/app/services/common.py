"""Serialization helpers shared by capability API services."""

from __future__ import annotations

import json
from typing import Any

from msm.api.http import (
    BulkActionDefinition,
    ResourceBooleanFilter,
    ResourceColumn,
    ResourceDescriptor,
    ResourceFilterOption,
    ResourceIdentity,
    ResourceListControls,
    ResourceListDiscovery,
    ResourceSearchControl,
    ResourceSelectFilter,
    ResourceTextFilter,
    build_resource_collection,
)

from ..schemas import ResourceCollection, ResourceDiscoveryResponse


def validate_page_window(*, limit: int, offset: int) -> None:
    """Require the offset/page-size relationship used by the static-site adapter."""
    if offset % limit:
        from ..errors import bad_request

        raise bad_request("offset must be an exact multiple of limit.")


def validate_ordering(ordering: str, *, allowed_fields: set[str]) -> None:
    key = ordering.removeprefix("-")
    if key not in allowed_fields:
        from ..errors import bad_request

        allowed = ", ".join(sorted(allowed_fields))
        raise bad_request(f"ordering must name one of: {allowed} (prefix with '-' for descending).")


def boolean_option(options: dict[str, Any], key: str, *, default: bool) -> bool:
    value = options.get(key, default)
    if not isinstance(value, bool):
        from ..errors import bad_request

        raise bad_request(f"options.{key} must be a boolean.")
    return value


def positive_float_option(
    options: dict[str, Any],
    key: str,
    *,
    default: float,
    maximum: float,
) -> float:
    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        from ..errors import bad_request

        raise bad_request(f"options.{key} must be a number.")
    normalized = float(value)
    if not 0 < normalized <= maximum:
        from ..errors import bad_request

        raise bad_request(f"options.{key} must be greater than 0 and at most {maximum:g}.")
    return normalized


def required_string_option(options: dict[str, Any], key: str) -> str:
    value = options.get(key)
    if not isinstance(value, str) or not value.strip():
        from ..errors import bad_request

        raise bad_request(f"options.{key} must be a non-empty string.")
    return value.strip()


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def coerce_asset_uid(asset_or_uid: Any) -> str:
    if isinstance(asset_or_uid, str):
        return asset_or_uid
    asset_uid = getattr(asset_or_uid, "uid", None)
    if asset_uid is not None:
        return str(asset_uid)
    asset_id = getattr(asset_or_uid, "id", None)
    if asset_id is not None:
        return str(asset_id)
    raise ValueError(f"Could not coerce asset uid from {asset_or_uid!r}")


def serialize_asset_mapping(raw_mapping: dict[str, Any]) -> dict[str, str]:
    return {
        symbol: coerce_asset_uid(asset_or_uid)
        for symbol, asset_or_uid in sorted(raw_mapping.items())
    }


def collection_response(
    *,
    items: list[Any],
    total: int,
    limit: int,
    offset: int,
) -> ResourceCollection:
    serialized = [
        json_safe(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
        for item in items
    ]
    response = build_resource_collection(
        items=serialized,
        limit=limit,
        offset=offset,
        total_items=total,
    )
    return ResourceCollection.model_validate(response.model_dump(by_alias=True))


def resource_discovery(
    *,
    resource_id: str,
    label: str,
    item_label: str,
    identity_fields: list[str],
    columns: list[dict[str, Any]],
    actions: list[dict[str, Any]] | None = None,
    searchable_fields: list[str] | None = None,
    filterable_fields: list[str] | None = None,
    filter_types: dict[str, str] | None = None,
    filter_options: dict[str, list[dict[str, Any]]] | None = None,
    orderable_fields: list[str] | None = None,
) -> ResourceDiscoveryResponse:
    """Build the installed Command Center SDK's canonical discovery envelope."""
    normalized_columns: list[ResourceColumn] = []
    for column in columns:
        raw_id = str(column["id"])
        normalized_column = {**column, "id": raw_id.replace("_", "-")}
        normalized_column.setdefault("value_path", raw_id.replace("-", "_"))
        normalized_column.setdefault("data_type", "text")
        normalized_columns.append(
            ResourceColumn.model_validate(
                {
                    "default_visible": True,
                    "hideable": True,
                    **normalized_column,
                }
            )
        )

    filters = []
    for field in filterable_fields or []:
        filter_label = field.replace("_", " ").title()
        if field in (filter_options or {}):
            filters.append(
                ResourceSelectFilter(
                    key=field,
                    label=filter_label,
                    options=[
                        ResourceFilterOption.model_validate(option)
                        for option in (filter_options or {})[field]
                    ],
                )
            )
        elif (filter_types or {}).get(field, "text") == "boolean":
            filters.append(ResourceBooleanFilter(key=field, label=filter_label))
        elif (filter_types or {}).get(field, "text") == "text":
            filters.append(ResourceTextFilter(key=field, label=filter_label))
        else:
            filter_type = (filter_types or {})[field]
            raise ValueError(f"Unsupported resource discovery filter type {filter_type!r}.")

    return ResourceDiscoveryResponse(
        resource=ResourceDescriptor(
            id=resource_id,
            label=label,
            item_label=item_label,
            identity=ResourceIdentity(fields=identity_fields),
        ),
        list=ResourceListDiscovery(
            controls=ResourceListControls(
                search=(
                    ResourceSearchControl(
                        placeholder=f"Search {label.lower()}",
                        fields=searchable_fields,
                    )
                    if searchable_fields
                    else None
                ),
                filters=filters,
                ordering=orderable_fields or [],
            ),
            columns=normalized_columns,
        ),
        bulk_actions=[BulkActionDefinition.model_validate(action) for action in actions or []],
    )


__all__ = [
    "boolean_option",
    "coerce_asset_uid",
    "collection_response",
    "json_safe",
    "positive_float_option",
    "resource_discovery",
    "serialize_asset_mapping",
    "validate_ordering",
    "validate_page_window",
]
