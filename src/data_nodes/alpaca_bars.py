from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import mainsequence.client as msc
import pandas as pd
from pydantic import Field
from mainsequence.tdag import DataNode, DataNodeConfiguration, DataNodeMetaData, RecordDefinition

from .alpaca_bars_support import (
    DEFAULT_BAR_SYMBOL_BATCH_SIZE,
    build_stock_historical_data_client,
    current_period_start,
    fetch_stock_bars_frame,
    group_bindings_by_last_update,
    normalize_adjustment,
    normalize_feed,
    normalize_frequency_id,
    normalize_stock_bars_frame,
    resolve_asset_bindings_from_category_assets,
    update_statistics_to_last_update_map,
)

UTC = dt.timezone.utc


class AlpacaStockBarsConfig(DataNodeConfiguration):
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
        json_schema_extra={"update_only": True},
    )
    records: list[RecordDefinition] | None = Field(
        default_factory=lambda: [
            RecordDefinition(
                column_name="open",
                dtype="float64",
                label="Open",
                description="Bar open price.",
            ),
            RecordDefinition(
                column_name="high",
                dtype="float64",
                label="High",
                description="Bar high price.",
            ),
            RecordDefinition(
                column_name="low",
                dtype="float64",
                label="Low",
                description="Bar low price.",
            ),
            RecordDefinition(
                column_name="close",
                dtype="float64",
                label="Close",
                description="Bar close price.",
            ),
            RecordDefinition(
                column_name="volume",
                dtype="float64",
                label="Volume",
                description="Reported traded share volume.",
            ),
            RecordDefinition(
                column_name="trade_count",
                dtype="float64",
                label="Trade Count",
                description="Number of trades in the bar.",
            ),
            RecordDefinition(
                column_name="vwap",
                dtype="float64",
                label="VWAP",
                description="Volume weighted average price in the bar.",
            ),
        ]
    )


class AlpacaStockBarsNode(DataNode):
    OFFSET_START = dt.datetime(2018, 1, 1, tzinfo=UTC)

    def __init__(self, config: AlpacaStockBarsConfig, *args, **kwargs):
        self.alpaca_stock_bars_config = config
        self.frequency_id = normalize_frequency_id(config.frequency_id)
        self.feed = normalize_feed(config.feed)
        self.adjustment = normalize_adjustment(config.adjustment)
        config.node_metadata = DataNodeMetaData(
            identifier=f"alpaca_stock_bars_{self.frequency_id}_{self.feed}_{self.adjustment}",
            description=(
                f"Alpaca US equity OHLCV bars at {self.frequency_id} frequency "
                f"from the {self.feed} feed with {self.adjustment} adjustment."
            ),
        )
        self._asset_bindings = None
        self._historical_client = None
        super().__init__(config=config, *args, **kwargs)

    def dependencies(self) -> dict[str, DataNode]:
        return {}

    def get_asset_list(self) -> list[msc.AssetMixin]:
        return [binding.asset for binding in self._resolve_asset_bindings()]

    def _coerce_asset_id(self, asset_or_id: int | msc.AssetMixin) -> int:
        if isinstance(asset_or_id, int):
            return asset_or_id
        asset_id = getattr(asset_or_id, "id", None)
        if isinstance(asset_id, int):
            return asset_id
        raise ValueError(f"Could not coerce asset id from {asset_or_id!r}")

    def _load_assets_from_category_unique_identifier(
        self,
        asset_category_unique_identifier: str,
    ) -> list[msc.AssetMixin]:
        asset_category = msc.AssetCategory.get_or_none(
            unique_identifier=asset_category_unique_identifier
        )
        if asset_category is None:
            raise ValueError(
                "Missing Alpaca stock bars asset category for DataNode config: "
                f"{asset_category_unique_identifier!r}"
            )

        category_asset_ids = [
            self._coerce_asset_id(asset_or_id) for asset_or_id in asset_category.assets
        ]
        if not category_asset_ids:
            return []

        assets = msc.Asset.filter(id__in=category_asset_ids)
        assets_by_id = {asset.id: asset for asset in assets}
        missing_asset_ids = [
            asset_id for asset_id in category_asset_ids if asset_id not in assets_by_id
        ]
        if missing_asset_ids:
            raise ValueError(
                "Asset category contains asset ids that could not be loaded: "
                f"{missing_asset_ids!r}"
            )
        return [assets_by_id[asset_id] for asset_id in category_asset_ids]

    def _resolve_asset_bindings(self):
        if self._asset_bindings is not None:
            return self._asset_bindings

        config = self.alpaca_stock_bars_config
        if config.asset_list:
            assets = list(config.asset_list)
        elif config.asset_category_unique_identifier:
            assets = self._load_assets_from_category_unique_identifier(
                config.asset_category_unique_identifier
            )
        else:
            raise ValueError(
                f"{self.__class__.__name__} requires either config.asset_list or "
                "config.asset_category_unique_identifier."
            )

        self._asset_bindings = resolve_asset_bindings_from_category_assets(assets=assets)
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

        update_statistics = self.update_statistics
        if update_statistics is None:
            raise RuntimeError("Update statistics were not set before update() was called.")

        last_update_by_unique_identifier = update_statistics_to_last_update_map(
            bindings=bindings,
            update_statistics=update_statistics,
        )
        grouped_bindings = group_bindings_by_last_update(
            bindings=bindings,
            last_update_by_unique_identifier=last_update_by_unique_identifier,
        )
        asset_unique_identifier_by_alpaca_symbol = {
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
                    unique_identifier_by_symbol=asset_unique_identifier_by_alpaca_symbol,
                    last_update_by_unique_identifier=last_update_by_unique_identifier,
                    period_cutoff=period_cutoff,
                )
                if not normalized_frame.empty:
                    output_frames.append(normalized_frame)

        if not output_frames:
            return pd.DataFrame()

        merged_output = pd.concat(output_frames).sort_index()
        merged_output = merged_output[~merged_output.index.duplicated(keep="last")]
        return merged_output


__all__ = [
    "AlpacaStockBarsConfig",
    "AlpacaStockBarsNode",
]
