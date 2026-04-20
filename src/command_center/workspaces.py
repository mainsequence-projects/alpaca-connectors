from __future__ import annotations

from types import UnionType
from typing import Any, Literal, get_args, get_origin

from api.app.schemas import (
    AssetRegistrationByTickerRequest,
    AssetRegistrationByTickerResponse,
    LightweightOhlcChartResponse,
)

ALPACA_ASSETS_REGISTRY_WORKSPACE_ID = 3
APP_COMPONENT_WIDGET_ID = "app-component"
LIGHTWEIGHT_CHARTS_SPEC_WIDGET_ID = "lightweight-charts-spec"
REGISTER_ASSET_BY_TICKER_WIDGET_INSTANCE_ID = "register-asset-by-ticker"
REGISTER_ASSET_BY_TICKER_ROUTE_PATH = "/v1/app-components/assets/register-ticker"
REGISTER_ASSET_BY_TICKER_OPERATION_KEY = (
    f"POST {REGISTER_ASSET_BY_TICKER_ROUTE_PATH}"
)
CHART_OHLC_APP_COMPONENT_WIDGET_INSTANCE_ID = "alpaca-ohlc-chart-request"
CHART_OHLC_WIDGET_INSTANCE_ID = "alpaca-ohlc-chart"
CHART_OHLC_ROUTE_PATH = "/v1/charts/lightweight/ohlc"
CHART_OHLC_OPERATION_KEY = f"POST {CHART_OHLC_ROUTE_PATH}"

CORE_VALUE_STRING_CONTRACT = "core.value.string@v1"
CORE_VALUE_NUMBER_CONTRACT = "core.value.number@v1"
CORE_VALUE_INTEGER_CONTRACT = "core.value.integer@v1"
CORE_VALUE_BOOLEAN_CONTRACT = "core.value.boolean@v1"
CORE_VALUE_JSON_CONTRACT = "core.value.json@v1"
CORE_VALUE_SCALAR_CONTRACTS = [
    CORE_VALUE_STRING_CONTRACT,
    CORE_VALUE_NUMBER_CONTRACT,
    CORE_VALUE_INTEGER_CONTRACT,
    CORE_VALUE_BOOLEAN_CONTRACT,
]


def _normalize_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (UnionType, getattr(__import__("typing"), "Union", None)):
        non_none_args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_none_args) == 1:
            return non_none_args[0]
    return annotation


def _widget_kind_for_annotation(annotation: Any) -> str:
    normalized = _normalize_annotation(annotation)
    origin = get_origin(normalized)

    if origin is Literal:
        return "enum"
    if normalized is bool:
        return "boolean"
    if normalized is int:
        return "integer"
    if normalized is float:
        return "number"
    if origin in (list, dict):
        return "json"
    return "string"


def _output_contract_for_kind(kind: str) -> str:
    if kind == "boolean":
        return CORE_VALUE_BOOLEAN_CONTRACT
    if kind == "integer":
        return CORE_VALUE_INTEGER_CONTRACT
    if kind == "number":
        return CORE_VALUE_NUMBER_CONTRACT
    if kind == "json":
        return CORE_VALUE_JSON_CONTRACT
    return CORE_VALUE_STRING_CONTRACT


def _input_accepts_for_kind(kind: str) -> list[str]:
    if kind == "json":
        return [CORE_VALUE_JSON_CONTRACT, *CORE_VALUE_SCALAR_CONTRACTS]
    return list(CORE_VALUE_SCALAR_CONTRACTS)


def _title_case_label(name: str) -> str:
    return name.replace("_", " ").title()


def _build_register_asset_binding_spec() -> dict[str, Any]:
    request_ports: list[dict[str, Any]] = []
    body_fields: list[dict[str, Any]] = []

    for field_name, field_info in AssetRegistrationByTickerRequest.model_fields.items():
        kind = _widget_kind_for_annotation(field_info.annotation)
        field_key = f"body:{field_name}"
        request_ports.append(
            {
                "id": field_key,
                "fieldKey": field_key,
                "label": field_info.title or _title_case_label(field_name),
                "description": field_info.description,
                "required": field_info.is_required(),
                "location": "body",
                "kind": kind,
                "accepts": _input_accepts_for_kind(kind),
            }
        )
        body_fields.append(
            {
                "key": field_key,
                "label": field_info.title or _title_case_label(field_name),
                "description": field_info.description,
                "location": "body",
                "required": field_info.is_required(),
                "kind": kind,
                "bodyPath": [field_name],
                "rootBodyValue": False,
                "contentType": "application/json",
                "defaultValue": None if field_info.is_required() else field_info.default,
                "exampleValue": None,
            }
        )

    response_ports: list[dict[str, Any]] = [
        {
            "id": "response:$",
            "label": "Response Body",
            "kind": "json",
            "contract": CORE_VALUE_JSON_CONTRACT,
            "responsePath": [],
            "statusCode": "200",
            "contentType": "application/json",
        }
    ]

    for field_name, field_info in AssetRegistrationByTickerResponse.model_fields.items():
        kind = _widget_kind_for_annotation(field_info.annotation)
        response_ports.append(
            {
                "id": f"response:{field_name}",
                "label": field_info.title or _title_case_label(field_name),
                "description": field_info.description,
                "kind": kind,
                "contract": _output_contract_for_kind(kind),
                "responsePath": [field_name],
                "statusCode": "200",
                "contentType": "application/json",
            }
        )

    return {
        "version": 1,
        "operationKey": REGISTER_ASSET_BY_TICKER_OPERATION_KEY,
        "requestPorts": request_ports,
        "responsePorts": response_ports,
        "requestForm": {
            "parameterFields": [],
            "bodyFields": body_fields,
            "bodyMode": "generated",
            "bodyContentType": "application/json",
            "bodyRequired": True,
        },
    }


def _build_register_asset_request_input_map() -> dict[str, Any]:
    return {
        "version": 1,
        "operationKey": REGISTER_ASSET_BY_TICKER_OPERATION_KEY,
        "fields": {
            "body:ticker": {
                "label": "Ticker",
            },
            "body:include_non_tradable": {
                "visibleOnCard": False,
                "prefillValue": "false",
            },
            "body:timeout": {
                "visibleOnCard": False,
                "prefillValue": "30",
            },
        },
    }


def _build_chart_query_field(
    *,
    name: str,
    label: str,
    description: str,
    required: bool,
    kind: str = "string",
    default_value: Any = None,
    hidden_from_form: bool = False,
    ui_enhancement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    field_key = f"query:{name}"
    field: dict[str, Any] = {
        "key": field_key,
        "label": label,
        "description": description,
        "location": "query",
        "required": required,
        "kind": kind,
        "paramName": name,
        "defaultValue": default_value,
        "exampleValue": None,
    }
    if hidden_from_form:
        field["hiddenFromForm"] = True
    if ui_enhancement is not None:
        field["uiEnhancement"] = ui_enhancement
    return field


def _build_chart_request_ports(parameter_fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": field["key"],
            "fieldKey": field["key"],
            "label": field["label"],
            "description": field["description"],
            "required": field["required"],
            "location": field["location"],
            "kind": field["kind"],
            "accepts": _input_accepts_for_kind(field["kind"]),
        }
        for field in parameter_fields
    ]


def _build_lightweight_ohlc_chart_binding_spec() -> dict[str, Any]:
    ticker_key = "query:ticker"
    page_key = "query:page"
    limit_key = "query:limit"
    parameter_fields = [
        _build_chart_query_field(
            name="ticker",
            label="Ticker",
            description=(
                "Ticker to resolve inside the configured holdings category."
            ),
            required=True,
            ui_enhancement={
                "role": "async-select-search",
                "widget": "select2",
                "selectionType": "single",
                "searchFieldKeys": [ticker_key],
                "pageFieldKey": page_key,
                "limitFieldKey": limit_key,
                "itemsPath": ["items"],
                "itemValueFieldPath": ["display"],
                "itemLabelFieldPath": ["label"],
                "paginationPath": ["pagination"],
                "paginationMoreField": "hasMore",
            },
        ),
        _build_chart_query_field(
            name="start_date",
            label="Start Date",
            description="Inclusive chart window start date.",
            required=False,
            kind="date",
        ),
        _build_chart_query_field(
            name="end_date",
            label="End Date",
            description="Inclusive chart window end date.",
            required=False,
            kind="date",
        ),
        _build_chart_query_field(
            name="page",
            label="Search Page",
            description="Async selector page.",
            required=False,
            kind="integer",
            default_value=1,
            hidden_from_form=True,
        ),
        _build_chart_query_field(
            name="limit",
            label="Search Limit",
            description="Async selector page size.",
            required=False,
            kind="integer",
            default_value=20,
            hidden_from_form=True,
        ),
    ]

    response_ports: list[dict[str, Any]] = [
        {
            "id": "response:$",
            "label": "Chart Response Body",
            "kind": "json",
            "contract": CORE_VALUE_JSON_CONTRACT,
            "responsePath": [],
            "statusCode": "200",
            "contentType": "application/json",
        }
    ]
    for field_name, field_info in LightweightOhlcChartResponse.model_fields.items():
        kind = _widget_kind_for_annotation(field_info.annotation)
        response_ports.append(
            {
                "id": f"response:{field_name}",
                "label": field_info.title or _title_case_label(field_name),
                "description": field_info.description,
                "kind": kind,
                "contract": _output_contract_for_kind(kind),
                "responsePath": [field_name],
                "statusCode": "200",
                "contentType": "application/json",
            }
        )

    return {
        "version": 1,
        "operationKey": CHART_OHLC_OPERATION_KEY,
        "requestPorts": _build_chart_request_ports(parameter_fields),
        "responsePorts": response_ports,
        "requestForm": {
            "parameterFields": parameter_fields,
            "bodyFields": [],
            "bodyMode": "none",
            "bodyContentType": None,
            "bodyRequired": False,
        },
    }


def _build_lightweight_ohlc_chart_request_input_map() -> dict[str, Any]:
    return {
        "version": 1,
        "operationKey": CHART_OHLC_OPERATION_KEY,
        "fields": {
            "query:ticker": {
                "label": "Ticker",
            },
            "query:start_date": {
                "label": "Start Date",
            },
            "query:end_date": {
                "label": "End Date",
            },
            "query:page": {
                "visibleOnCard": False,
                "prefillValue": "1",
            },
            "query:limit": {
                "visibleOnCard": False,
                "prefillValue": "20",
            },
        },
    }


def build_register_asset_by_ticker_app_component_widget(
    *,
    fastapi_release_id: int,
    instance_id: str = REGISTER_ASSET_BY_TICKER_WIDGET_INSTANCE_ID,
) -> dict[str, Any]:
    if fastapi_release_id <= 0:
        raise ValueError("fastapi_release_id must be a positive integer.")

    return {
        "id": instance_id,
        "widgetId": APP_COMPONENT_WIDGET_ID,
        "title": "Register Asset By Ticker",
        "props": {
            "apiTargetMode": "main-sequence-resource-release",
            "mainSequenceResourceRelease": {
                "releaseId": fastapi_release_id,
            },
            "authMode": "session-jwt",
            "method": "post",
            "path": REGISTER_ASSET_BY_TICKER_ROUTE_PATH,
            "requestBodyContentType": "application/json",
            "bindingSpec": _build_register_asset_binding_spec(),
            "requestInputMap": _build_register_asset_request_input_map(),
            "showHeader": True,
            "showResponse": True,
            "hideRequestButton": False,
            "requestButtonLabel": "Register",
            "refreshOnDashboardRefresh": False,
        },
        "layout": {
            "cols": 6,
            "rows": 10,
        },
        "position": {
            "x": 0,
            "y": 0,
        },
    }


def build_lightweight_ohlc_chart_app_component_widget(
    *,
    fastapi_release_id: int,
    instance_id: str = CHART_OHLC_APP_COMPONENT_WIDGET_INSTANCE_ID,
) -> dict[str, Any]:
    if fastapi_release_id <= 0:
        raise ValueError("fastapi_release_id must be a positive integer.")

    return {
        "id": instance_id,
        "widgetId": APP_COMPONENT_WIDGET_ID,
        "title": "Load OHLC Chart",
        "props": {
            "apiTargetMode": "main-sequence-resource-release",
            "mainSequenceResourceRelease": {
                "releaseId": fastapi_release_id,
            },
            "authMode": "session-jwt",
            "method": "post",
            "path": CHART_OHLC_ROUTE_PATH,
            "bindingSpec": _build_lightweight_ohlc_chart_binding_spec(),
            "requestInputMap": _build_lightweight_ohlc_chart_request_input_map(),
            "showHeader": True,
            "showResponse": False,
            "hideRequestButton": False,
            "requestButtonLabel": "Load Chart",
            "refreshOnDashboardRefresh": False,
        },
        "layout": {
            "cols": 6,
            "rows": 10,
        },
        "position": {
            "x": 6,
            "y": 0,
        },
    }


def build_lightweight_ohlc_chart_widget(
    *,
    instance_id: str = CHART_OHLC_WIDGET_INSTANCE_ID,
    source_widget_id: str = CHART_OHLC_APP_COMPONENT_WIDGET_INSTANCE_ID,
) -> dict[str, Any]:
    return {
        "id": instance_id,
        "widgetId": LIGHTWEIGHT_CHARTS_SPEC_WIDGET_ID,
        "title": "OHLC Chart",
        "props": {
            "spec": {
                "fitContent": True,
                "series": [],
            },
        },
        "bindings": {
            "props-json": {
                "sourceWidgetId": source_widget_id,
                "sourceOutputId": "response:$",
            }
        },
        "layout": {
            "cols": 12,
            "rows": 18,
        },
        "position": {
            "x": 0,
            "y": 10,
        },
    }


def build_alpaca_assets_registry_workspace_update(
    *,
    fastapi_release_id: int,
) -> dict[str, Any]:
    return {
        "title": "Alpaca Assets Registry",
        "description": (
            "Workspace for Alpaca-backed asset registration and holdings management."
        ),
        "labels": ["alpaca", "assets", "registry"],
        "category": "Markets",
        "source": "user",
        "schemaVersion": 1,
        "layoutKind": "custom",
        "grid": {
            "columns": 12,
            "rowHeight": 18,
            "gap": 2,
        },
        "autoGrid": {},
        "companions": [],
        "controls": {
            "refresh": {
                "enabled": False,
            }
        },
        "widgets": [
            build_register_asset_by_ticker_app_component_widget(
                fastapi_release_id=fastapi_release_id,
            ),
            build_lightweight_ohlc_chart_app_component_widget(
                fastapi_release_id=fastapi_release_id,
            ),
            build_lightweight_ohlc_chart_widget(),
        ],
    }
