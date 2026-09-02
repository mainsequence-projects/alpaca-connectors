from __future__ import annotations

import json
from typing import Any

from src.assets.alpaca_us_equities import (
    build_alpaca_us_equity_registration_plan,
    register_alpaca_us_equity_assets,
    resolve_alpaca_us_equity_registration_plan,
)
from src.etf_holdings import (
    ETF_PROVIDER_MAP_NORMALIZED,
    ETFS_MAIN_TICKERS,
    MAG_7_CATEGORY_SYMBOLS,
    SUPPORTED_COMPONENT_PROVIDERS,
    EtfExpansionRequest,
    expand_etf_seed_symbols,
)
from src.holdings_categories import (
    build_holdings_asset_category_plan,
    sync_holdings_asset_category,
)

from .schemas import (
    AssetRegistrationDiscoveryResponse,
    AssetRegistrationExecuteResponse,
    AssetRegistrationRequest,
    DiscoveryConfigResponse,
    HoldingsCategoryExecuteResponse,
    HoldingsCategoryPlanResponse,
    HoldingsCategoryRequest,
)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _coerce_asset_uid(asset_or_uid: Any) -> str:
    if isinstance(asset_or_uid, str):
        return asset_or_uid
    asset_uid = getattr(asset_or_uid, "uid", None)
    if asset_uid is not None:
        return str(asset_uid)
    asset_id = getattr(asset_or_uid, "id", None)
    if asset_id is not None:
        return str(asset_id)
    raise ValueError(f"Could not coerce asset uid from {asset_or_uid!r}")


def _serialize_asset_mapping(raw_mapping: dict[str, Any]) -> dict[str, str]:
    return {
        symbol: _coerce_asset_uid(asset_or_uid)
        for symbol, asset_or_uid in sorted(raw_mapping.items())
    }


def _resolve_asset_registration_symbols(
    request: AssetRegistrationRequest,
) -> tuple[list[str] | None, Any | None]:
    if request.symbols:
        return request.symbols, None

    expansion_result = expand_etf_seed_symbols(
        EtfExpansionRequest(
            seed_tickers=request.seed_tickers or [],
            component_provider=request.component_provider or "",
            timeout=request.timeout,
        )
    )
    return expansion_result.symbols_for_registration, expansion_result


def _asset_registration_plan_summary(plan: Any, expansion_result: Any | None) -> dict[str, Any]:
    summary = plan.summary()
    if expansion_result is None:
        return summary

    return {
        **summary,
        "seed_symbols": expansion_result.universe.seed_symbols,
        "component_provider": expansion_result.component_provider,
        "expanded_candidate_symbols": expansion_result.universe.expanded_symbols,
        "expanded_component_symbols_by_seed": (expansion_result.universe.component_symbols_by_seed),
        "unsupported_seed_symbols": expansion_result.universe.unsupported_seed_symbols,
    }


def build_asset_registration_discovery(
    request: AssetRegistrationRequest,
) -> AssetRegistrationDiscoveryResponse:
    symbols, expansion_result = _resolve_asset_registration_symbols(request)
    plan = build_alpaca_us_equity_registration_plan(
        symbols=symbols,
        include_non_tradable=request.include_non_tradable,
        timeout=request.timeout,
    )
    resolution = resolve_alpaca_us_equity_registration_plan(
        plan,
        timeout=request.timeout,
    )
    return AssetRegistrationDiscoveryResponse(
        request=request,
        plan_summary=_asset_registration_plan_summary(plan, expansion_result),
        resolution_summary=resolution.summary(),
        can_register=not bool(plan.unresolved_symbols or plan.missing_symbols_from_alpaca),
        unresolved_symbols=list(plan.unresolved_symbols),
        missing_symbols_from_alpaca=list(plan.missing_symbols_from_alpaca),
        missing_symbols_to_register=sorted(match.symbol for match in resolution.missing_matches),
        warnings_by_symbol=dict(sorted(plan.warnings_by_symbol.items())),
    )


def execute_asset_registration(
    request: AssetRegistrationRequest,
) -> AssetRegistrationExecuteResponse:
    symbols, expansion_result = _resolve_asset_registration_symbols(request)
    plan = build_alpaca_us_equity_registration_plan(
        symbols=symbols,
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
    return AssetRegistrationExecuteResponse(
        request=request,
        plan_summary=_asset_registration_plan_summary(plan, expansion_result),
        resolution_summary=resolution.summary(),
        assets_by_symbol=_serialize_asset_mapping(registration_result["assets"]),
        existing_asset_uids_by_symbol=_serialize_asset_mapping(
            registration_result["existing_assets"]
        ),
        created_asset_uids_by_symbol=_serialize_asset_mapping(
            registration_result["created_assets"]
        ),
        unresolved_symbols=list(registration_result["unresolved_symbols"]),
        not_registered_missing_figi_symbols=list(
            registration_result["not_registered_missing_figi_symbols"]
        ),
        not_registered_missing_alpaca_symbols=list(
            registration_result["not_registered_missing_alpaca_symbols"]
        ),
        warnings_by_symbol=dict(sorted(registration_result["warnings_by_symbol"].items())),
    )


def build_holdings_category_discovery(
    request: HoldingsCategoryRequest,
) -> HoldingsCategoryPlanResponse:
    plan = build_holdings_asset_category_plan(
        etf_ticker=request.etf_ticker,
        component_provider=request.component_provider,
        include_non_tradable=request.include_non_tradable,
        timeout=request.timeout,
    )
    return HoldingsCategoryPlanResponse(
        request=request,
        plan_summary=_json_safe(plan.summary()),
        has_blockers=plan.has_blockers(),
        missing_registered_symbols=list(plan.missing_registered_symbols),
        missing_symbols_from_alpaca=[],
        unresolved_symbols=[],
    )


def execute_holdings_category_sync(
    request: HoldingsCategoryRequest,
) -> HoldingsCategoryExecuteResponse:
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
        asset_uids=[
            plan.existing_asset_uids_by_symbol[symbol]
            for symbol in plan.component_symbols
            if symbol in plan.existing_asset_uids_by_symbol
        ],
    )
    return HoldingsCategoryExecuteResponse(
        request=request,
        plan_summary=_json_safe(plan.summary()),
        sync_result={
            "unique_identifier": sync_result.unique_identifier,
            "display_name": sync_result.display_name,
            "asset_uids": [str(asset_uid) for asset_uid in sync_result.asset_uids],
            "asset_count": len(sync_result.asset_uids),
        },
    )


def get_discovery_config() -> DiscoveryConfigResponse:
    return DiscoveryConfigResponse(
        supported_component_providers=list(SUPPORTED_COMPONENT_PROVIDERS),
        etf_provider_map_normalized=dict(sorted(ETF_PROVIDER_MAP_NORMALIZED.items())),
        mag_7_category_symbols=list(MAG_7_CATEGORY_SYMBOLS),
        etfs_main_tickers=list(ETFS_MAIN_TICKERS),
    )
