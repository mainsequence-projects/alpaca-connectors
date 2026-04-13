from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from src.assets.alpaca_us_equities import fetch_alpaca_us_equities
from src.settings import (
    OPENFIGI_DEFAULT_TIMEOUT,
    OPENFIGI_MAPPING_URL,
    get_alpaca_api_key,
    get_alpaca_secret_key,
    get_openfigi_api_key,
)

UTC = dt.timezone.utc
SUPPORTED_ALPACA_BAR_FREQUENCIES = (
    "1m",
    "5m",
    "15m",
    "30m",
    "1h",
    "1d",
)
SUPPORTED_ALPACA_DATA_FEEDS = tuple(feed.value for feed in DataFeed)
SUPPORTED_ALPACA_ADJUSTMENTS = tuple(adjustment.value for adjustment in Adjustment)
DEFAULT_BAR_SYMBOL_BATCH_SIZE = 200


@dataclass(frozen=True)
class AlpacaBarAssetBinding:
    asset: Any
    unique_identifier: str
    alpaca_symbol: str


def _chunked(values: Sequence[str], chunk_size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), chunk_size):
        yield list(values[start : start + chunk_size])


def _build_symbol_alias_candidates(symbol: str) -> list[str]:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        return []

    candidates = [normalized_symbol]
    alias_candidates = [
        normalized_symbol.replace("/", ".").replace("-", "."),
        normalized_symbol.replace(".", "/").replace("-", "/"),
        normalized_symbol.replace(".", "-").replace("/", "-"),
    ]
    if all(separator not in normalized_symbol for separator in (".", "/", "-")) and len(
        normalized_symbol
    ) >= 3:
        base_symbol = normalized_symbol[:-1]
        share_class_suffix = normalized_symbol[-1]
        alias_candidates.extend(
            [
                f"{base_symbol}.{share_class_suffix}",
                f"{base_symbol}/{share_class_suffix}",
                f"{base_symbol}-{share_class_suffix}",
            ]
        )

    for candidate in alias_candidates:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def build_stock_historical_data_client() -> StockHistoricalDataClient:
    api_key = get_alpaca_api_key()
    secret_key = get_alpaca_secret_key()
    if not api_key or not secret_key:
        raise RuntimeError(
            "Missing Alpaca credentials in the environment. Set ALPACA_API_KEY/"
            "ALPACA_SECRET_KEY."
        )
    return StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)


def normalize_frequency_id(frequency_id: str) -> str:
    normalized_frequency = frequency_id.strip().lower()
    aliases = {
        "1min": "1m",
        "1mins": "1m",
        "5min": "5m",
        "5mins": "5m",
        "15min": "15m",
        "15mins": "15m",
        "30min": "30m",
        "30mins": "30m",
        "60m": "1h",
        "1hr": "1h",
        "1hour": "1h",
        "1day": "1d",
        "1days": "1d",
    }
    normalized_frequency = aliases.get(normalized_frequency, normalized_frequency)
    if normalized_frequency not in SUPPORTED_ALPACA_BAR_FREQUENCIES:
        raise ValueError(
            f"Unsupported Alpaca bar frequency {frequency_id!r}. Supported values: "
            f"{', '.join(SUPPORTED_ALPACA_BAR_FREQUENCIES)}."
        )
    return normalized_frequency


def frequency_id_to_timeframe(frequency_id: str) -> TimeFrame:
    normalized_frequency = normalize_frequency_id(frequency_id)
    mapping = {
        "1m": TimeFrame.Minute,
        "5m": TimeFrame(5, TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "30m": TimeFrame(30, TimeFrameUnit.Minute),
        "1h": TimeFrame.Hour,
        "1d": TimeFrame.Day,
    }
    return mapping[normalized_frequency]


def frequency_id_to_timedelta(frequency_id: str) -> dt.timedelta:
    normalized_frequency = normalize_frequency_id(frequency_id)
    mapping = {
        "1m": dt.timedelta(minutes=1),
        "5m": dt.timedelta(minutes=5),
        "15m": dt.timedelta(minutes=15),
        "30m": dt.timedelta(minutes=30),
        "1h": dt.timedelta(hours=1),
        "1d": dt.timedelta(days=1),
    }
    return mapping[normalized_frequency]


def normalize_feed(feed: str) -> str:
    normalized_feed = feed.strip().lower()
    if normalized_feed not in SUPPORTED_ALPACA_DATA_FEEDS:
        raise ValueError(
            f"Unsupported Alpaca data feed {feed!r}. Supported values: "
            f"{', '.join(SUPPORTED_ALPACA_DATA_FEEDS)}."
        )
    return normalized_feed


def normalize_adjustment(adjustment: str) -> str:
    normalized_adjustment = adjustment.strip().lower()
    if normalized_adjustment not in SUPPORTED_ALPACA_ADJUSTMENTS:
        raise ValueError(
            f"Unsupported Alpaca adjustment {adjustment!r}. Supported values: "
            f"{', '.join(SUPPORTED_ALPACA_ADJUSTMENTS)}."
        )
    return normalized_adjustment


def current_period_start(now_utc: dt.datetime, frequency_id: str) -> dt.datetime:
    normalized_frequency = normalize_frequency_id(frequency_id)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    else:
        now_utc = now_utc.astimezone(UTC)

    if normalized_frequency == "1d":
        return now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    if normalized_frequency == "1h":
        return now_utc.replace(minute=0, second=0, microsecond=0)

    minutes = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
    }[normalized_frequency]
    floored_minute = (now_utc.minute // minutes) * minutes
    return now_utc.replace(minute=floored_minute, second=0, microsecond=0)


def build_alpaca_symbol_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for alpaca_asset in fetch_alpaca_us_equities(include_non_tradable=False):
        for candidate in _build_symbol_alias_candidates(alpaca_asset.symbol):
            lookup[candidate] = alpaca_asset.symbol
    return lookup


def _build_openfigi_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = get_openfigi_api_key()
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    return headers


def query_openfigi_ticker_by_figi(figis: Sequence[str]) -> dict[str, str]:
    normalized_figis = sorted({figi.strip().upper() for figi in figis if figi and figi.strip()})
    if not normalized_figis:
        return {}

    response = requests.post(
        OPENFIGI_MAPPING_URL,
        headers=_build_openfigi_headers(),
        json=[{"idType": "ID_BB_GLOBAL", "idValue": figi} for figi in normalized_figis],
        timeout=OPENFIGI_DEFAULT_TIMEOUT,
    )
    response.raise_for_status()

    resolved_tickers_by_figi: dict[str, str] = {}
    for figi, payload in zip(normalized_figis, response.json(), strict=True):
        data = payload.get("data") or []
        if not data:
            continue
        ticker = data[0].get("ticker")
        if ticker:
            resolved_tickers_by_figi[figi] = str(ticker).strip().upper()
    return resolved_tickers_by_figi


def resolve_asset_bindings_from_category_assets(
    *,
    assets: Sequence[Any],
) -> list[AlpacaBarAssetBinding]:
    symbol_lookup = build_alpaca_symbol_lookup()
    bindings: list[AlpacaBarAssetBinding] = []
    unresolved_assets: list[Any] = []

    for asset in assets:
        ticker = getattr(asset, "ticker", None)
        if ticker is None and getattr(asset, "current_snapshot", None) is not None:
            ticker = asset.current_snapshot.ticker
        if not ticker:
            unresolved_assets.append(asset)
            continue

        resolved_symbol = None
        for candidate in _build_symbol_alias_candidates(str(ticker)):
            resolved_symbol = symbol_lookup.get(candidate)
            if resolved_symbol is not None:
                break

        if resolved_symbol is None:
            unresolved_assets.append(asset)
            continue

        bindings.append(
            AlpacaBarAssetBinding(
                asset=asset,
                unique_identifier=asset.unique_identifier,
                alpaca_symbol=resolved_symbol,
            )
        )

    if unresolved_assets:
        figi_fallback_tickers = query_openfigi_ticker_by_figi(
            [asset.figi or asset.unique_identifier for asset in unresolved_assets]
        )
        remaining_unresolved_assets: list[Any] = []
        for asset in unresolved_assets:
            figi = asset.figi or asset.unique_identifier
            figi_ticker = figi_fallback_tickers.get(figi)
            if not figi_ticker:
                remaining_unresolved_assets.append(asset)
                continue

            resolved_symbol = None
            for candidate in _build_symbol_alias_candidates(figi_ticker):
                resolved_symbol = symbol_lookup.get(candidate)
                if resolved_symbol is not None:
                    break

            if resolved_symbol is None:
                remaining_unresolved_assets.append(asset)
                continue

            bindings.append(
                AlpacaBarAssetBinding(
                    asset=asset,
                    unique_identifier=asset.unique_identifier,
                    alpaca_symbol=resolved_symbol,
                )
            )

        unresolved_assets = remaining_unresolved_assets

    if unresolved_assets:
        raise ValueError(
            "These category assets could not be resolved to Alpaca symbols: "
            f"{sorted(asset.unique_identifier for asset in unresolved_assets)!r}"
        )

    return bindings


def build_stock_bars_request(
    *,
    symbols: Sequence[str],
    frequency_id: str,
    start: dt.datetime,
    end: dt.datetime,
    feed: str,
    adjustment: str,
) -> StockBarsRequest:
    return StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=frequency_id_to_timeframe(frequency_id),
        start=start,
        end=end,
        feed=DataFeed(normalize_feed(feed)),
        adjustment=Adjustment(normalize_adjustment(adjustment)),
    )


def fetch_stock_bars_frame(
    *,
    client: StockHistoricalDataClient,
    symbol_batch: Sequence[str],
    frequency_id: str,
    start: dt.datetime,
    end: dt.datetime,
    feed: str,
    adjustment: str,
) -> pd.DataFrame:
    if not symbol_batch:
        return pd.DataFrame()
    request = build_stock_bars_request(
        symbols=symbol_batch,
        frequency_id=frequency_id,
        start=start,
        end=end,
        feed=feed,
        adjustment=adjustment,
    )
    bar_set = client.get_stock_bars(request)
    return bar_set.df.copy()


def normalize_stock_bars_frame(
    *,
    frame: pd.DataFrame,
    unique_identifier_by_symbol: dict[str, str],
    last_update_by_unique_identifier: dict[str, dt.datetime],
    period_cutoff: dt.datetime,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    normalized = frame.reset_index().rename(columns={"timestamp": "time_index"})
    normalized["symbol"] = normalized["symbol"].astype(str).str.upper()
    normalized["time_index"] = pd.to_datetime(normalized["time_index"], utc=True)
    normalized["unique_identifier"] = normalized["symbol"].map(unique_identifier_by_symbol)
    normalized = normalized[normalized["unique_identifier"].notna()].copy()
    normalized = normalized[normalized["time_index"] < period_cutoff].copy()
    if normalized.empty:
        return pd.DataFrame()

    normalized["last_update"] = normalized["unique_identifier"].map(last_update_by_unique_identifier)
    normalized = normalized[normalized["time_index"] > normalized["last_update"]].copy()
    if normalized.empty:
        return pd.DataFrame()

    for column_name in ("open", "high", "low", "close", "volume", "trade_count", "vwap"):
        if column_name not in normalized.columns:
            normalized[column_name] = pd.NA

    normalized = normalized[
        [
            "time_index",
            "unique_identifier",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trade_count",
            "vwap",
        ]
    ].copy()
    for column_name in ("open", "high", "low", "close", "volume", "trade_count", "vwap"):
        normalized[column_name] = pd.to_numeric(normalized[column_name], errors="coerce")

    normalized = normalized.drop_duplicates(
        subset=["time_index", "unique_identifier"],
        keep="last",
    )
    normalized = normalized.sort_values(["time_index", "unique_identifier"])
    normalized = normalized.set_index(["time_index", "unique_identifier"])
    normalized.index = normalized.index.set_names(["time_index", "unique_identifier"])
    return normalized


def group_bindings_by_last_update(
    *,
    bindings: Sequence[AlpacaBarAssetBinding],
    last_update_by_unique_identifier: dict[str, dt.datetime],
) -> dict[dt.datetime, list[AlpacaBarAssetBinding]]:
    grouped_bindings: dict[dt.datetime, list[AlpacaBarAssetBinding]] = defaultdict(list)
    for binding in bindings:
        grouped_bindings[last_update_by_unique_identifier[binding.unique_identifier]].append(binding)
    return dict(sorted(grouped_bindings.items(), key=lambda item: item[0]))


def update_statistics_to_last_update_map(
    *,
    bindings: Sequence[AlpacaBarAssetBinding],
    update_statistics: Any,
) -> dict[str, dt.datetime]:
    return {
        binding.unique_identifier: update_statistics.get_last_update_index_2d(binding.unique_identifier)
        for binding in bindings
    }


__all__ = [
    "AlpacaBarAssetBinding",
    "DEFAULT_BAR_SYMBOL_BATCH_SIZE",
    "SUPPORTED_ALPACA_ADJUSTMENTS",
    "SUPPORTED_ALPACA_BAR_FREQUENCIES",
    "SUPPORTED_ALPACA_DATA_FEEDS",
    "build_stock_historical_data_client",
    "current_period_start",
    "fetch_stock_bars_frame",
    "frequency_id_to_timedelta",
    "group_bindings_by_last_update",
    "normalize_adjustment",
    "normalize_feed",
    "normalize_frequency_id",
    "normalize_stock_bars_frame",
    "resolve_asset_bindings_from_category_assets",
    "update_statistics_to_last_update_map",
]
