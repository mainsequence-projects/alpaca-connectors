"""Alpaca bar request, normalization, and asset-binding helpers."""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from src.assets.alpaca_us_equities import fetch_alpaca_us_equities

UTC = dt.timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
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
ALPACA_ASSET_IDENTIFIER_PREFIX = "ALPACA::"


@dataclass(frozen=True)
class AssetTickerFigi:
    """Lightweight asset-resolution record fed to the symbol binder.

    Replaces the old SDK ``Asset`` object. The ticker comes from required
    ``AlpacaAssetDetails``; FIGI is optional reference metadata only. The canonical
    ``unique_identifier`` is the Alpaca-UUID-backed identity used in storage.
    """

    unique_identifier: str
    ticker: str | None = None
    figi: str | None = None


@dataclass(frozen=True)
class AlpacaBarAssetBinding:
    asset: Any
    unique_identifier: str
    alpaca_symbol: str


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
    if (
        all(separator not in normalized_symbol for separator in (".", "/", "-"))
        and len(normalized_symbol) >= 3
    ):
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


def build_alpaca_symbol_lookup(*, trading_client=None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for alpaca_asset in fetch_alpaca_us_equities(
        trading_client=trading_client,
    ):
        for candidate in _build_symbol_alias_candidates(alpaca_asset.symbol):
            lookup[candidate] = alpaca_asset.symbol
    return lookup


def _is_canonical_alpaca_asset_identifier(unique_identifier: str) -> bool:
    if not unique_identifier.startswith(ALPACA_ASSET_IDENTIFIER_PREFIX):
        return False
    provider_uid = unique_identifier.removeprefix(ALPACA_ASSET_IDENTIFIER_PREFIX)
    try:
        uuid.UUID(provider_uid)
    except ValueError:
        return False
    return True


def resolve_asset_bindings_from_category_assets(
    *,
    assets: Sequence[Any],
    trading_client=None,
) -> list[AlpacaBarAssetBinding]:
    symbol_lookup = build_alpaca_symbol_lookup(trading_client=trading_client)
    bindings: list[AlpacaBarAssetBinding] = []
    unresolved_assets: list[Any] = []

    for asset in assets:
        ticker = getattr(asset, "ticker", None)
        if not ticker:
            unresolved_assets.append(asset)
            continue

        resolved_symbol = None
        for candidate in _build_symbol_alias_candidates(str(ticker)):
            resolved_symbol = symbol_lookup.get(candidate)
            if resolved_symbol is not None:
                break

        # The required AlpacaAssetDetails row is the durable provider identity
        # mapping. A symbol can disappear from Alpaca's current catalog while its
        # canonical Alpaca UUID and last known provider symbol remain valid for
        # historical data. Reuse that stored symbol only for an exact canonical
        # Alpaca identity; FIGI is never an identity fallback.
        if resolved_symbol is None and _is_canonical_alpaca_asset_identifier(
            str(asset.unique_identifier)
        ):
            resolved_symbol = str(ticker).strip().upper()

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
    frequency_id: str,
    asset_identifier_by_symbol: dict[str, str],
    last_update_by_asset_identifier: dict[str, dt.datetime],
    period_cutoff: dt.datetime,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    normalized = frame.reset_index().rename(columns={"timestamp": "bar_start_time"})
    normalized["symbol"] = normalized["symbol"].astype(str).str.upper()
    normalized["bar_start_time"] = pd.to_datetime(normalized["bar_start_time"], utc=True).astype(
        "datetime64[ns, UTC]"
    )
    if normalize_frequency_id(frequency_id) == "1d":
        session_date = normalized["bar_start_time"].dt.tz_convert(NEW_YORK).dt.date
        session_open = [
            dt.datetime.combine(
                current_date,
                dt.time(hour=9, minute=30),
                tzinfo=NEW_YORK,
            ).astimezone(UTC)
            for current_date in session_date
        ]
        session_close = [
            dt.datetime.combine(
                current_date,
                dt.time(hour=16, minute=0),
                tzinfo=NEW_YORK,
            ).astimezone(UTC)
            for current_date in session_date
        ]
        normalized["open_time"] = pd.to_datetime(session_open, utc=True).astype(
            "datetime64[ns, UTC]"
        )
        normalized["time_index"] = pd.to_datetime(session_close, utc=True).astype(
            "datetime64[ns, UTC]"
        )
    else:
        bar_interval = frequency_id_to_timedelta(frequency_id)
        normalized["open_time"] = normalized["bar_start_time"]
        normalized["time_index"] = (
            normalized["bar_start_time"] + pd.Timedelta(bar_interval)
        ).astype("datetime64[ns, UTC]")
    normalized["asset_identifier"] = normalized["symbol"].map(asset_identifier_by_symbol)
    normalized = normalized[normalized["asset_identifier"].notna()].copy()
    normalized = normalized[normalized["bar_start_time"] < period_cutoff].copy()
    if normalized.empty:
        return pd.DataFrame()

    normalized["last_update"] = normalized["asset_identifier"].map(last_update_by_asset_identifier)
    normalized = normalized[normalized["time_index"] > normalized["last_update"]].copy()
    if normalized.empty:
        return pd.DataFrame()

    for column_name in ("open", "high", "low", "close", "volume", "trade_count", "vwap"):
        if column_name not in normalized.columns:
            normalized[column_name] = pd.NA

    normalized = normalized[
        [
            "time_index",
            "asset_identifier",
            "open_time",
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
        subset=["time_index", "asset_identifier"],
        keep="last",
    )
    normalized = normalized.sort_values(["time_index", "asset_identifier"])
    normalized = normalized.set_index(["time_index", "asset_identifier"])
    normalized.index = normalized.index.set_names(["time_index", "asset_identifier"])
    normalized.index = pd.MultiIndex.from_arrays(
        [
            normalized.index.get_level_values("time_index").astype("datetime64[ns, UTC]"),
            normalized.index.get_level_values("asset_identifier"),
        ],
        names=["time_index", "asset_identifier"],
    )
    return normalized


def group_bindings_by_last_update(
    *,
    bindings: Sequence[AlpacaBarAssetBinding],
    last_update_by_asset_identifier: dict[str, dt.datetime],
) -> dict[dt.datetime, list[AlpacaBarAssetBinding]]:
    """Bucket bindings by their shared per-asset last-update timestamp.

    Each bucket is fetched with a single Alpaca ``request_start``, preserving the legacy
    batching. The ``last_update_by_asset_identifier`` map is now derived from
    ``AssetIndexedDataNode.get_asset_update_range_map_great_or_equal()`` instead of the removed
    ``UpdateStatistics.get_last_update_index_2d``.
    """
    grouped_bindings: dict[dt.datetime, list[AlpacaBarAssetBinding]] = defaultdict(list)
    for binding in bindings:
        grouped_bindings[last_update_by_asset_identifier[binding.unique_identifier]].append(binding)
    return dict(sorted(grouped_bindings.items(), key=lambda item: item[0]))


__all__ = [
    "AlpacaBarAssetBinding",
    "AssetTickerFigi",
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
]
