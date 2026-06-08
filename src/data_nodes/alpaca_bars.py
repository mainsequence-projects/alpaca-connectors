from __future__ import annotations

import datetime as dt

import pandas as pd
from pydantic import Field, field_validator

from msm.data_nodes.assets import (
    ASSET_IDENTIFIER_DIMENSION,
    AssetIndexedDataNode,
    AssetIndexedDataNodeConfiguration,
)

from src.assets.resolution import (
    asset_unique_identifiers_for_category,
    ticker_figi_for_unique_identifier,
)
from src.markets_storage.alpaca_bars import AlpacaStockBars1dSipAllStorage, storage_for

from .alpaca_bars_support import (
    DEFAULT_BAR_SYMBOL_BATCH_SIZE,
    AssetTickerFigi,
    build_stock_historical_data_client,
    current_period_start,
    fetch_stock_bars_frame,
    group_bindings_by_last_update,
    normalize_adjustment,
    normalize_feed,
    normalize_frequency_id,
    normalize_stock_bars_frame,
    resolve_asset_bindings_from_category_assets,
)

UTC = dt.timezone.utc


class AlpacaStockBarsConfig(AssetIndexedDataNodeConfiguration):
    """Updater-scope configuration for Alpaca OHLCV stock bars.

    Storage-first: the published column schema, table identifier, and ``asset_identifier``
    foreign key live on the storage class (``src.markets_storage.alpaca_bars``), NOT here. This
    config carries only the fields that select the table meaning (``frequency_id``/``feed``/
    ``adjustment``) and the updater universe (inherited ``asset_list`` plus
    ``asset_category_unique_identifier``). Every field is hashed into ``update_hash`` /
    ``storage_hash``; there is no ``update_only`` escape hatch.
    """

    frequency_id: str = Field(
        ...,
        description="Alpaca stock bar frequency identifier such as 1m, 5m, 1h, or 1d.",
    )
    feed: str = Field(
        ...,
        description="Alpaca market data feed, for example iex or sip.",
    )
    adjustment: str = Field(
        ...,
        description="Alpaca adjustment mode, for example raw, split, dividend, or all.",
    )
    asset_category_unique_identifier: str | None = Field(
        default=None,
        description="Optional AssetCategory unique_identifier used to resolve the updater universe.",
    )

    @field_validator("frequency_id")
    @classmethod
    def _normalize_frequency_id(cls, value: str) -> str:
        return normalize_frequency_id(value)

    @field_validator("feed")
    @classmethod
    def _normalize_feed(cls, value: str) -> str:
        return normalize_feed(value)

    @field_validator("adjustment")
    @classmethod
    def _normalize_adjustment(cls, value: str) -> str:
        return normalize_adjustment(value)


class AlpacaStockBarsNode(AssetIndexedDataNode):
    """Asset-indexed Alpaca OHLCV bars producer.

    Subclasses ``AssetIndexedDataNode`` (not ``AssetTimestampedDataNode``) because it *fetches*
    from Alpaca during ``update()`` rather than receiving pre-built frames. The base supplies the
    universe scoping and per-asset incremental ``update_statistics`` that the old node hand-rolled.
    """

    # Same value the base already defines; kept explicit so the 2018 first-run floor is obvious.
    OFFSET_START = dt.datetime(2018, 1, 1, tzinfo=UTC)

    @classmethod
    def _required_storage_table(cls):
        """Default storage table for class-level identifier/description derivation.

        Instances bind the per-``(frequency_id, feed, adjustment)`` storage table resolved in
        ``__init__``; this default only seeds ``_default_identifier()`` at the class level.
        """
        return AlpacaStockBars1dSipAllStorage

    def __init__(self, config: AlpacaStockBarsConfig, **kwargs):
        self._asset_bindings = None
        self._historical_client = None
        storage_table = storage_for(config.frequency_id, config.feed, config.adjustment)
        super().__init__(config=config, storage_table=storage_table, **kwargs)

    @property
    def frequency_id(self) -> str:
        return self.config.frequency_id

    @property
    def feed(self) -> str:
        return self.config.feed

    @property
    def adjustment(self) -> str:
        return self.config.adjustment

    def dependencies(self) -> dict:
        return {}

    def get_asset_list(self) -> list[str]:
        """Resolve the updater universe to a list of asset unique identifiers (strings)."""
        config = self.config
        if config.asset_list:
            return self.validate_asset_list(list(config.asset_list))
        if config.asset_category_unique_identifier:
            return self.validate_asset_list(
                asset_unique_identifiers_for_category(config.asset_category_unique_identifier)
            )
        raise ValueError(
            f"{type(self).__name__} requires either config.asset_list or "
            "config.asset_category_unique_identifier."
        )

    def _resolve_asset_bindings(self):
        if self._asset_bindings is not None:
            return self._asset_bindings

        unique_identifiers = [
            self._asset_unique_identifier(item) for item in (self.get_asset_list() or [])
        ]
        records = []
        for unique_identifier in unique_identifiers:
            ticker, figi = ticker_figi_for_unique_identifier(unique_identifier)
            records.append(
                AssetTickerFigi(unique_identifier=unique_identifier, ticker=ticker, figi=figi)
            )

        self._asset_bindings = resolve_asset_bindings_from_category_assets(assets=records)
        return self._asset_bindings

    def _get_historical_client(self):
        if self._historical_client is None:
            self._historical_client = build_stock_historical_data_client()
        return self._historical_client

    def update(self) -> pd.DataFrame:
        bindings = self._resolve_asset_bindings()
        if not bindings:
            return pd.DataFrame()

        now_utc = dt.datetime.now(tz=UTC)
        period_cutoff = current_period_start(now_utc, self.frequency_id)
        if period_cutoff <= self.get_offset_start():
            return pd.DataFrame()

        # Per-asset incremental start dates from the base. This is a FLAT
        # {asset_identifier: {"start_date": ...}} map (offset-start fallback already applied for
        # never-seen assets); it does not bucket, so we re-derive request groups below.
        asset_range_map = self.get_asset_update_range_map_great_or_equal()
        offset_start = self.get_offset_start()
        last_update_by_asset_identifier = {
            binding.unique_identifier: self._range_start(
                asset_range_map, binding.unique_identifier, offset_start
            )
            for binding in bindings
        }

        grouped_bindings = group_bindings_by_last_update(
            bindings=bindings,
            last_update_by_asset_identifier=last_update_by_asset_identifier,
        )
        asset_identifier_by_alpaca_symbol = {
            binding.alpaca_symbol: binding.unique_identifier for binding in bindings
        }
        historical_client = self._get_historical_client()
        output_frames: list[pd.DataFrame] = []

        for last_update, grouped_asset_bindings in grouped_bindings.items():
            request_start = last_update
            if request_start >= period_cutoff:
                continue

            alpaca_symbol_batch = [binding.alpaca_symbol for binding in grouped_asset_bindings]
            for chunk in (
                alpaca_symbol_batch[start : start + DEFAULT_BAR_SYMBOL_BATCH_SIZE]
                for start in range(0, len(alpaca_symbol_batch), DEFAULT_BAR_SYMBOL_BATCH_SIZE)
            ):
                batch_frame = fetch_stock_bars_frame(
                    client=historical_client,
                    symbol_batch=chunk,
                    frequency_id=self.frequency_id,
                    start=request_start,
                    end=now_utc,
                    feed=self.feed,
                    adjustment=self.adjustment,
                )
                normalized_frame = normalize_stock_bars_frame(
                    frame=batch_frame,
                    frequency_id=self.frequency_id,
                    asset_identifier_by_symbol=asset_identifier_by_alpaca_symbol,
                    last_update_by_asset_identifier=last_update_by_asset_identifier,
                    period_cutoff=period_cutoff,
                )
                if not normalized_frame.empty:
                    output_frames.append(normalized_frame)

        if not output_frames:
            return pd.DataFrame()

        merged_output = pd.concat(output_frames).sort_index()
        merged_output = merged_output[~merged_output.index.duplicated(keep="last")]
        return merged_output

    @staticmethod
    def _range_start(asset_range_map, asset_identifier: str, offset_start: dt.datetime):
        info = asset_range_map.get(asset_identifier)
        if info is None:
            return offset_start
        start_date = info["start_date"] if "start_date" in info else None
        return start_date or offset_start


__all__ = [
    "ASSET_IDENTIFIER_DIMENSION",
    "AlpacaStockBarsConfig",
    "AlpacaStockBarsNode",
]
