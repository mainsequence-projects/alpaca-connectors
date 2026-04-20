from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pandas as pd

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
from .schemas import (
    AssetRegistrationByTickerRequest,
    AssetRegistrationByTickerResponse,
    AssetRegistrationRequest,
    AssetSearchSelectOption,
    AssetSearchSelectPagination,
    AssetSearchSelectResponse,
    HoldingsCategoryRequest,
    LightweightOhlcChartRequest,
    LightweightOhlcChartResponse,
)

DEFAULT_CHART_ASSET_CATEGORY_UNIQUE_IDENTIFIER = "HOLDINGS__IVV"


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


def _single_symbol_plan_symbol(plan: Any, requested_ticker: str) -> str | None:
    if plan.alpaca_assets:
        return plan.alpaca_assets[0].symbol
    return plan.requested_symbol_aliases.get(requested_ticker)


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


def execute_asset_registration_by_ticker(
    request: AssetRegistrationByTickerRequest,
) -> AssetRegistrationByTickerResponse:
    plan = build_alpaca_us_equity_registration_plan(
        symbols=[request.ticker],
        include_non_tradable=request.include_non_tradable,
        timeout=request.timeout,
    )
    resolution = resolve_alpaca_us_equity_registration_plan(
        plan,
        timeout=request.timeout,
    )

    resolved_symbol = _single_symbol_plan_symbol(plan, request.ticker)
    alpaca_asset = next(iter(plan.alpaca_assets), None)
    match = plan.matches_by_symbol.get(resolved_symbol) if resolved_symbol else None

    if request.ticker in plan.missing_symbols_from_alpaca:
        return AssetRegistrationByTickerResponse(
            requested_ticker=request.ticker,
            alpaca_symbol=None,
            alpaca_name=None,
            figi=None,
            classification_pass_name=None,
            security_type=None,
            security_type_2=None,
            exchange_code=None,
            status="blocked_missing_alpaca",
            asset_id=None,
            created=False,
            already_registered=False,
            missing_from_alpaca=True,
            missing_figi=False,
            warnings=[],
        )

    if match is None:
        warning = None
        if resolved_symbol:
            warning = plan.warnings_by_symbol.get(resolved_symbol)
        if warning is None:
            warning = plan.warnings_by_symbol.get(request.ticker)
        return AssetRegistrationByTickerResponse(
            requested_ticker=request.ticker,
            alpaca_symbol=resolved_symbol,
            alpaca_name=alpaca_asset.name if alpaca_asset else None,
            figi=None,
            classification_pass_name=None,
            security_type=None,
            security_type_2=None,
            exchange_code=None,
            status="blocked_missing_figi",
            asset_id=None,
            created=False,
            already_registered=False,
            missing_from_alpaca=False,
            missing_figi=True,
            warnings=[warning] if warning else [],
        )

    registration_result = register_alpaca_us_equity_assets(
        registration_resolution=resolution,
        timeout=request.timeout,
    )
    created_asset = registration_result["created_assets"].get(match.symbol)
    existing_asset = registration_result["existing_assets"].get(match.symbol)
    asset_or_id = created_asset if created_asset is not None else existing_asset
    asset_id = _coerce_asset_id(asset_or_id) if asset_or_id is not None else None

    return AssetRegistrationByTickerResponse(
        requested_ticker=request.ticker,
        alpaca_symbol=match.symbol,
        alpaca_name=alpaca_asset.name if alpaca_asset else None,
        figi=match.figi,
        classification_pass_name=match.classification_pass_name,
        security_type=match.security_type,
        security_type_2=match.security_type_2,
        exchange_code=match.exchange_code,
        status="created" if created_asset is not None else "existing",
        asset_id=asset_id,
        created=created_asset is not None,
        already_registered=created_asset is None and existing_asset is not None,
        missing_from_alpaca=False,
        missing_figi=False,
        warnings=[],
    )


def execute_lightweight_ohlc_chart(
    request: LightweightOhlcChartRequest,
) -> LightweightOhlcChartResponse:
    from mainsequence.client.models_tdag import DataNodeStorage

    start_dt = dt.datetime.combine(request.start_date, dt.time.min, tzinfo=dt.UTC)
    end_dt = dt.datetime.combine(request.end_date, dt.time.max, tzinfo=dt.UTC)
    bars_frame, _ = DataNodeStorage.get_data_between_dates_from_node_identifier(
        node_identifier=request.node_identifier,
        start_date=start_dt,
        end_date=end_dt,
        unique_identifier_list=[request.unique_identifier],
        columns=["open", "high", "low", "close", "volume"],
    )

    if bars_frame.empty:
        raise ValueError(
            "No OHLC bars found for "
            f"{request.unique_identifier!r} in {request.node_identifier!r} "
            f"between {request.start_date.isoformat()} and {request.end_date.isoformat()}."
        )

    normalized = bars_frame.copy()
    normalized["time_index"] = pd.to_datetime(normalized["time_index"], utc=True, errors="coerce")
    normalized = normalized[
        normalized["time_index"].notna()
        & normalized["open"].notna()
        & normalized["high"].notna()
        & normalized["low"].notna()
        & normalized["close"].notna()
    ].copy()

    if normalized.empty:
        raise ValueError(
            "No valid OHLC rows remained after normalization for "
            f"{request.unique_identifier!r}."
        )

    normalized = normalized.sort_values("time_index")

    ohlc_series_data: list[dict[str, Any]] = []
    volume_series_data: list[dict[str, Any]] = []

    for _, row in normalized.iterrows():
        bar_time = row["time_index"].date().isoformat()
        open_value = float(row["open"])
        high_value = float(row["high"])
        low_value = float(row["low"])
        close_value = float(row["close"])
        volume_value = float(row["volume"]) if pd.notna(row.get("volume")) else 0.0
        is_up_bar = close_value >= open_value

        ohlc_series_data.append(
            {
                "time": bar_time,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
            }
        )
        volume_series_data.append(
            {
                "time": bar_time,
                "value": volume_value,
                "color": "$theme.positive" if is_up_bar else "$theme.negative",
            }
        )

    spec = {
        "chartOptions": {
            "layout": {
                "background": {"type": "solid", "color": {"$themeToken": "background", "alpha": 1}},
                "textColor": "$theme.muted-foreground",
            },
            "grid": {
                "vertLines": {"color": {"$themeToken": "chart-grid", "alpha": 0.08}},
                "horzLines": {"color": {"$themeToken": "chart-grid", "alpha": 0.08}},
            },
            "rightPriceScale": {"borderColor": "$theme.border"},
            "timeScale": {"borderColor": "$theme.border", "timeVisible": True},
        },
        "fitContent": True,
        "series": [
            {
                "id": "ohlc",
                "type": "candlestick",
                "options": {
                    "upColor": "$theme.positive",
                    "downColor": "$theme.negative",
                    "wickUpColor": "$theme.positive",
                    "wickDownColor": "$theme.negative",
                    "borderVisible": False,
                },
                "data": ohlc_series_data,
            },
            {
                "id": "volume",
                "type": "histogram",
                "paneIndex": 1,
                "options": {"priceFormat": {"type": "volume"}, "priceScaleId": ""},
                "data": volume_series_data,
            },
        ],
    }
    return LightweightOhlcChartResponse(
        unique_identifier=request.unique_identifier,
        node_identifier=request.node_identifier,
        start_date=request.start_date,
        end_date=request.end_date,
        point_count=len(ohlc_series_data),
        spec=spec,
        spec_json=json.dumps(spec, separators=(",", ":"), ensure_ascii=True),
    )


def _asset_snapshot_value(asset: Any, field_name: str) -> Any:
    current_snapshot = getattr(asset, "current_snapshot", None)
    if isinstance(current_snapshot, dict):
        value = current_snapshot.get(field_name)
        if value is not None:
            return value
    if current_snapshot is not None:
        value = getattr(current_snapshot, field_name, None)
        if value is not None:
            return value
    return getattr(asset, field_name, None)


def _asset_search_text(asset: Any) -> str:
    values = [
        getattr(asset, "unique_identifier", None),
        getattr(asset, "figi", None),
        _asset_snapshot_value(asset, "ticker"),
        _asset_snapshot_value(asset, "name"),
        _asset_snapshot_value(asset, "asset_name"),
        _asset_snapshot_value(asset, "company_name"),
    ]
    return " ".join(str(value).upper() for value in values if value)


def _asset_search_option(asset: Any) -> AssetSearchSelectOption | None:
    unique_identifier = getattr(asset, "unique_identifier", None)
    if not isinstance(unique_identifier, str) or not unique_identifier.strip():
        return None

    ticker = _asset_snapshot_value(asset, "ticker")
    name = (
        _asset_snapshot_value(asset, "name")
        or _asset_snapshot_value(asset, "asset_name")
        or _asset_snapshot_value(asset, "company_name")
    )
    figi = getattr(asset, "figi", None)
    display_parts = [str(value) for value in (ticker, name) if value]
    display = " - ".join(display_parts) if display_parts else unique_identifier
    label = str(ticker) if ticker else unique_identifier
    return AssetSearchSelectOption(
        unique_identifier=unique_identifier,
        label=label,
        ticker=str(ticker) if ticker else None,
        name=str(name) if name else None,
        figi=str(figi) if figi else None,
        display=display,
    )


def search_assets_for_lightweight_ohlc_select(
    *,
    query: str,
    asset_category_unique_identifier: str = DEFAULT_CHART_ASSET_CATEGORY_UNIQUE_IDENTIFIER,
    page: int = 1,
    limit: int = 20,
) -> AssetSearchSelectResponse:
    if page < 1:
        raise ValueError("page must be greater than or equal to 1.")
    if limit < 1:
        raise ValueError("limit must be greater than or equal to 1.")

    normalized_query = query.strip().upper()
    if not normalized_query:
        return AssetSearchSelectResponse(
            query="",
            asset_category_unique_identifier=asset_category_unique_identifier,
            items=[],
            pagination=AssetSearchSelectPagination(page=page, limit=limit, hasMore=False),
        )

    import mainsequence.client as msc

    category = msc.AssetCategory.get_or_none(
        unique_identifier=asset_category_unique_identifier,
    )
    if category is None:
        raise ValueError(
            "Asset category not found: "
            f"{asset_category_unique_identifier!r}. Create it before using ticker search."
        )

    asset_ids = [_coerce_asset_id(asset_or_id) for asset_or_id in category.assets]
    if not asset_ids:
        return AssetSearchSelectResponse(
            query=normalized_query,
            asset_category_unique_identifier=asset_category_unique_identifier,
            items=[],
            pagination=AssetSearchSelectPagination(page=page, limit=limit, hasMore=False),
        )

    matching_assets = msc.Asset.filter(id__in=asset_ids)
    options = [
        option
        for asset in matching_assets
        if normalized_query in _asset_search_text(asset)
        for option in [_asset_search_option(asset)]
        if option is not None
    ]
    options.sort(key=lambda option: (option.ticker or "", option.unique_identifier))

    offset = (page - 1) * limit
    paged_options = options[offset : offset + limit]
    return AssetSearchSelectResponse(
        query=normalized_query,
        asset_category_unique_identifier=asset_category_unique_identifier,
        items=paged_options,
        pagination=AssetSearchSelectPagination(
            page=page,
            limit=limit,
            hasMore=offset + limit < len(options),
        ),
    )


def resolve_lightweight_ohlc_asset_unique_identifier(
    *,
    identifier: str,
    asset_category_unique_identifier: str = DEFAULT_CHART_ASSET_CATEGORY_UNIQUE_IDENTIFIER,
) -> str:
    normalized_identifier = identifier.strip().upper()
    if not normalized_identifier:
        raise ValueError("asset identifier must not be empty.")

    import mainsequence.client as msc

    category = msc.AssetCategory.get_or_none(
        unique_identifier=asset_category_unique_identifier,
    )
    if category is None:
        raise ValueError(
            "Asset category not found: "
            f"{asset_category_unique_identifier!r}. Create it before using chart search."
        )

    asset_ids = [_coerce_asset_id(asset_or_id) for asset_or_id in category.assets]
    if not asset_ids:
        raise ValueError(
            "Asset category is empty: "
            f"{asset_category_unique_identifier!r}. Add assets before using chart search."
        )

    matching_assets = msc.Asset.filter(id__in=asset_ids)
    candidates: list[tuple[str, str | None, str | None]] = []
    for asset in matching_assets:
        unique_identifier = getattr(asset, "unique_identifier", None)
        if not isinstance(unique_identifier, str) or not unique_identifier.strip():
            continue

        ticker = _asset_snapshot_value(asset, "ticker")
        figi = getattr(asset, "figi", None)
        normalized_values = {
            str(value).strip().upper()
            for value in (unique_identifier, ticker, figi)
            if value
        }
        if normalized_identifier in normalized_values:
            candidates.append((
                unique_identifier,
                str(ticker) if ticker else None,
                str(figi) if figi else None,
            ))

    if len(candidates) == 1:
        return candidates[0][0]
    if len(candidates) > 1:
        raise ValueError(
            f"Identifier {identifier!r} matched multiple assets in "
            f"{asset_category_unique_identifier!r}."
        )
    raise ValueError(
        f"Identifier {identifier!r} was not found in "
        f"{asset_category_unique_identifier!r} by unique_identifier, ticker, or FIGI."
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
