from __future__ import annotations

import datetime as dt
from typing import Any

from src.assets.alpaca_us_equities import (
    build_alpaca_us_equity_registration_plan,
    register_alpaca_us_equity_assets,
    resolve_alpaca_us_equity_registration_plan,
)
from src.holdings_categories import (
    build_holdings_asset_category_plan,
    sync_holdings_asset_category,
)
from src.settings import (
    ETF_PROVIDER_MAP_NORMALIZED,
    ETFS_MAIN_TICKERS,
    MAG_7_CATEGORY_SYMBOLS,
    SUPPORTED_COMPONENT_PROVIDERS,
)

from .command_center_models import (
    DataNodeTableSourceInputResponse,
    SourceMetadataResponse,
    TableFieldResponse,
)
from .schemas import AssetRegistrationRequest, HoldingsCategoryRequest


def _coerce_asset_id(asset_or_id: Any) -> int:
    if isinstance(asset_or_id, int):
        return asset_or_id
    asset_id = getattr(asset_or_id, "id", None)
    if isinstance(asset_id, int):
        return asset_id
    raise ValueError(f"Could not coerce asset id from {asset_or_id!r}")


def _serialize_asset_mapping(raw_mapping: dict[str, Any]) -> dict[str, int]:
    return {
        symbol: _coerce_asset_id(asset_or_id)
        for symbol, asset_or_id in sorted(raw_mapping.items())
    }


def _field_type_for_value(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dt.datetime):
        return "datetime"
    if isinstance(value, list | dict):
        return "json"
    if value is None:
        return "unknown"
    return "string"


def _build_table_source_response(
    *,
    label: str,
    rows: list[dict[str, Any]],
) -> DataNodeTableSourceInputResponse:
    columns = list(rows[0].keys()) if rows else []
    fields = [
        TableFieldResponse(
            key=column,
            label=column.replace("_", " ").title(),
            type=_field_type_for_value(rows[0].get(column)),
            provenance="manual",
        )
        for column in columns
    ]
    return DataNodeTableSourceInputResponse(
        status="ready",
        columns=columns,
        rows=rows,
        fields=fields,
        source=SourceMetadataResponse(
            kind="custom-api",
            label=label,
            updatedAtMs=dt.datetime.now(tz=dt.UTC),
        ),
    )


def build_asset_registration_discovery(
    request: AssetRegistrationRequest,
) -> DataNodeTableSourceInputResponse:
    plan = build_alpaca_us_equity_registration_plan(
        symbols=request.symbols,
        seed_tickers=request.seed_tickers,
        component_provider=request.component_provider,
        include_non_tradable=request.include_non_tradable,
        timeout=request.timeout,
    )
    resolution = resolve_alpaca_us_equity_registration_plan(
        plan,
        timeout=request.timeout,
    )
    missing_symbols_to_register = sorted(match.symbol for match in resolution.missing_matches)
    can_register = not bool(plan.unresolved_symbols or plan.missing_symbols_from_alpaca)
    return _build_table_source_response(
        label="Asset Registration Plan",
        rows=[
            {
                "request": request.model_dump(mode="json"),
                "plan_summary": plan.summary(),
                "resolution_summary": resolution.summary(),
                "can_register": can_register,
                "unresolved_symbols": list(plan.unresolved_symbols),
                "missing_symbols_from_alpaca": list(plan.missing_symbols_from_alpaca),
                "missing_symbols_to_register": missing_symbols_to_register,
                "warnings_by_symbol": dict(sorted(plan.warnings_by_symbol.items())),
            }
        ],
    )


def execute_asset_registration(
    request: AssetRegistrationRequest,
) -> DataNodeTableSourceInputResponse:
    plan = build_alpaca_us_equity_registration_plan(
        symbols=request.symbols,
        seed_tickers=request.seed_tickers,
        component_provider=request.component_provider,
        include_non_tradable=request.include_non_tradable,
        timeout=request.timeout,
    )
    resolution = resolve_alpaca_us_equity_registration_plan(
        plan,
        timeout=request.timeout,
    )
    registration_result = register_alpaca_us_equity_assets(
        registration_resolution=resolution,
        timeout=request.timeout,
    )
    return _build_table_source_response(
        label="Asset Registration Execute",
        rows=[
            {
                "request": request.model_dump(mode="json"),
                "plan_summary": plan.summary(),
                "resolution_summary": resolution.summary(),
                "assets_by_symbol": _serialize_asset_mapping(registration_result["assets"]),
                "existing_asset_ids_by_symbol": _serialize_asset_mapping(
                    registration_result["existing_assets"]
                ),
                "created_asset_ids_by_symbol": _serialize_asset_mapping(
                    registration_result["created_assets"]
                ),
                "unresolved_symbols": list(registration_result["unresolved_symbols"]),
                "not_registered_missing_figi_symbols": list(
                    registration_result["not_registered_missing_figi_symbols"]
                ),
                "not_registered_missing_alpaca_symbols": list(
                    registration_result["not_registered_missing_alpaca_symbols"]
                ),
                "warnings_by_symbol": dict(sorted(registration_result["warnings_by_symbol"].items())),
            }
        ],
    )


def build_holdings_category_discovery(
    request: HoldingsCategoryRequest,
) -> DataNodeTableSourceInputResponse:
    plan = build_holdings_asset_category_plan(
        etf_ticker=request.etf_ticker,
        component_provider=request.component_provider,
        include_non_tradable=request.include_non_tradable,
        timeout=request.timeout,
    )
    return _build_table_source_response(
        label="Holdings Category Plan",
        rows=[
            {
                "request": request.model_dump(mode="json"),
                "plan_summary": plan.summary(),
                "has_blockers": plan.has_blockers(),
                "missing_registered_symbols": list(plan.missing_registered_symbols),
                "missing_symbols_from_alpaca": list(plan.registration_plan.missing_symbols_from_alpaca),
                "unresolved_symbols": list(plan.registration_plan.unresolved_symbols),
            }
        ],
    )


def execute_holdings_category_sync(
    request: HoldingsCategoryRequest,
) -> DataNodeTableSourceInputResponse:
    plan = build_holdings_asset_category_plan(
        etf_ticker=request.etf_ticker,
        component_provider=request.component_provider,
        include_non_tradable=request.include_non_tradable,
        timeout=request.timeout,
    )
    if plan.has_blockers():
        raise ValueError(
            "Refusing to sync the holdings category because extracted holdings are incomplete "
            "or not fully registered in MainSequence."
        )
    sync_result = sync_holdings_asset_category(
        etf_ticker=plan.etf_ticker,
        asset_ids=[
            plan.existing_asset_ids_by_symbol[symbol]
            for symbol in plan.component_symbols
            if symbol in plan.existing_asset_ids_by_symbol
        ],
    )
    return _build_table_source_response(
        label="Holdings Category Execute",
        rows=[
            {
                "request": request.model_dump(mode="json"),
                "plan_summary": plan.summary(),
                "sync_result": {
                    "unique_identifier": sync_result.unique_identifier,
                    "display_name": sync_result.display_name,
                    "asset_ids": sync_result.asset_ids,
                    "asset_count": len(sync_result.asset_ids),
                },
            }
        ],
    )


def get_discovery_config() -> DataNodeTableSourceInputResponse:
    return _build_table_source_response(
        label="Discovery Configuration",
        rows=[
            {
                "supported_component_providers": list(SUPPORTED_COMPONENT_PROVIDERS),
                "etf_provider_map_normalized": dict(sorted(ETF_PROVIDER_MAP_NORMALIZED.items())),
                "mag_7_category_symbols": list(MAG_7_CATEGORY_SYMBOLS),
                "etfs_main_tickers": list(ETFS_MAIN_TICKERS),
            }
        ],
    )
