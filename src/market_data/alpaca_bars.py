from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

import pandas as pd
from msm.data_nodes.assets import (
    ASSET_IDENTIFIER_DIMENSION,
    AssetIndexedDataNode,
    AssetIndexedDataNodeConfiguration,
)
from pydantic import Field, field_validator, model_validator

from src.assets.resolution import (
    asset_unique_identifiers_for_category,
    ticker_and_optional_figi_by_unique_identifiers,
)

from .alpaca_bars_support import (
    DEFAULT_BAR_SYMBOL_BATCH_SIZE,
    AssetTickerFigi,
    current_period_start,
    fetch_stock_bars_frame,
    group_bindings_by_last_update,
    normalize_adjustment,
    normalize_feed,
    normalize_frequency_id,
    normalize_stock_bars_frame,
    resolve_asset_bindings_from_category_assets,
)
from .storage import AlpacaStockBars1dSipAllStorage, storage_for

UTC = dt.timezone.utc


class AlpacaStockBarsConfig(AssetIndexedDataNodeConfiguration):
    """Updater-scope configuration for Alpaca OHLCV stock bars.

    Storage-first: the published column schema, table identifier, and ``asset_identifier``
    foreign key live on the storage class (``src.market_data.storage``), NOT here. This
    config carries only the fields that select the table meaning and the source identity. For an
    explicit-asset source, the inherited ``asset_list`` is hashed. For a universe, its stable
    category identifier is hashed. For account holdings, the Account UID is hashed while the
    newest eligible snapshot is resolved dynamically. Storage identity lives on the selected
    storage class.
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
    asset_source: Literal["assets", "universe", "account_holdings"] = Field(
        ...,
        description="Asset resolution strategy for this updater identity.",
    )
    asset_category_unique_identifier: str | None = Field(
        default=None,
        description="Optional AssetCategory unique_identifier used to resolve the updater universe.",
    )
    account_uid: uuid.UUID | None = Field(
        default=None,
        description="Account UID used only as the dynamic account_holdings source identity.",
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

    @model_validator(mode="after")
    def _validate_asset_source(self) -> "AlpacaStockBarsConfig":
        if self.asset_source == "assets":
            if not self.asset_list:
                raise ValueError("assets source requires a non-empty asset_list.")
            if self.asset_category_unique_identifier or self.account_uid:
                raise ValueError(
                    "assets source forbids asset_category_unique_identifier and account_uid."
                )
        elif self.asset_source == "universe":
            if not self.asset_category_unique_identifier or self.asset_list or self.account_uid:
                raise ValueError(
                    "universe source requires asset_category_unique_identifier and forbids "
                    "asset_list and account_uid."
                )
        elif not self.account_uid or self.asset_list or self.asset_category_unique_identifier:
            raise ValueError(
                "account_holdings source requires account_uid and forbids asset_list and "
                "asset_category_unique_identifier."
            )
        return self


class AlpacaStockBarsNode(AssetIndexedDataNode):
    """Asset-indexed Alpaca OHLCV bars producer.

    Subclasses ``AssetIndexedDataNode`` (not ``AssetTimestampedDataNode``) because it *fetches*
    from Alpaca during ``update()`` rather than receiving pre-built frames. The base supplies the
    universe scoping and per-asset incremental ``update_statistics`` that the old node hand-rolled.
    """

    # Same value the base already defines; kept explicit so the 2018 first-run floor is obvious.
    OFFSET_START = dt.datetime(2018, 1, 1, tzinfo=UTC)

    @classmethod
    def _required_output_table(cls):
        """Default output table for class-level identifier/description derivation.

        Instances bind the per-``(frequency_id, feed, adjustment)`` output table resolved in
        ``__init__``; this default only seeds ``_default_identifier()`` at the class level.
        """
        return AlpacaStockBars1dSipAllStorage

    def __init__(
        self,
        config: AlpacaStockBarsConfig,
        *,
        historical_client=None,
        trading_client=None,
        resolved_asset_identifiers: list[str] | None = None,
        **kwargs,
    ):
        self._asset_bindings = None
        self._historical_client = historical_client
        self._trading_client = trading_client
        self._resolved_asset_identifiers = resolved_asset_identifiers
        output_table = storage_for(config.frequency_id, config.feed, config.adjustment)
        super().__init__(config=config, output_table=output_table, **kwargs)

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
        if self.config.asset_source == "account_holdings":
            from msm.data_nodes.accounts.storage import AccountHoldingsStorage

            from mainsequence.meta_tables import TimeIndexTableRef

            return {
                "account_holdings": TimeIndexTableRef.from_meta_table(
                    AccountHoldingsStorage.get_time_index_meta_table()
                )
            }
        return {}

    def get_asset_list(self) -> list[str]:
        """Resolve the updater universe to a list of asset unique identifiers (strings)."""
        config = self.config
        if self._resolved_asset_identifiers is not None:
            return self.validate_asset_list(self._resolved_asset_identifiers)
        if config.asset_source == "assets":
            return self.validate_asset_list(list(config.asset_list or []))
        if config.asset_source == "universe":
            return self.validate_asset_list(
                asset_unique_identifiers_for_category(config.asset_category_unique_identifier)
            )
        from src.holdings import resolve_recent_account_holdings_assets

        resolved = resolve_recent_account_holdings_assets(config.account_uid)
        return self.validate_asset_list(resolved.asset_identifiers)

    def _resolve_asset_bindings(self):
        if self._asset_bindings is not None:
            return self._asset_bindings

        unique_identifiers = [
            self._asset_unique_identifier(item) for item in (self.get_asset_list() or [])
        ]
        identity_details = ticker_and_optional_figi_by_unique_identifiers(unique_identifiers)
        records = [
            AssetTickerFigi(
                unique_identifier=unique_identifier,
                ticker=identity_details.get(unique_identifier, (None, None))[0],
                figi=identity_details.get(unique_identifier, (None, None))[1],
            )
            for unique_identifier in unique_identifiers
        ]

        self._asset_bindings = resolve_asset_bindings_from_category_assets(
            assets=records,
            trading_client=self._trading_client,
        )
        return self._asset_bindings

    def _get_historical_client(self):
        if self._historical_client is None:
            raise RuntimeError(
                "AlpacaStockBarsNode requires an account-resolved historical_client."
            )
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
                    # Only request finalized periods. Asking through ``now`` is
                    # both unnecessary (normalization drops the open period) and
                    # can trigger Alpaca's recent-SIP entitlement restriction.
                    end=period_cutoff,
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
