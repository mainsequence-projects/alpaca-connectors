from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd

from src.market_data.alpaca_bars_support import (
    current_period_start,
    normalize_frequency_id,
    normalize_stock_bars_frame,
    resolve_asset_bindings_from_category_assets,
)

UTC = dt.timezone.utc


class AlpacaBarsSupportTests(unittest.TestCase):
    def test_normalize_frequency_id_accepts_aliases(self) -> None:
        self.assertEqual(normalize_frequency_id("1day"), "1d")
        self.assertEqual(normalize_frequency_id("1hour"), "1h")
        self.assertEqual(normalize_frequency_id("15min"), "15m")

    def test_current_period_start_floors_minute_frequency(self) -> None:
        now_utc = dt.datetime(2026, 4, 13, 10, 17, 45, tzinfo=UTC)
        self.assertEqual(
            current_period_start(now_utc, "5m"),
            dt.datetime(2026, 4, 13, 10, 15, tzinfo=UTC),
        )

    def test_normalize_stock_bars_frame_filters_duplicates_and_last_updates(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "AAPL",
                    "timestamp": pd.Timestamp("2026-04-10T04:00:00Z"),
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1_000.0,
                    "trade_count": 10.0,
                    "vwap": 100.2,
                },
                {
                    "symbol": "AAPL",
                    "timestamp": pd.Timestamp("2026-04-11T04:00:00Z"),
                    "open": 101.0,
                    "high": 102.0,
                    "low": 100.0,
                    "close": 101.5,
                    "volume": 1_100.0,
                    "trade_count": 11.0,
                    "vwap": 101.2,
                },
                {
                    "symbol": "AAPL",
                    "timestamp": pd.Timestamp("2026-04-11T04:00:00Z"),
                    "open": 101.1,
                    "high": 102.1,
                    "low": 100.1,
                    "close": 101.6,
                    "volume": 1_111.0,
                    "trade_count": 12.0,
                    "vwap": 101.3,
                },
            ]
        ).set_index(["symbol", "timestamp"])

        normalized = normalize_stock_bars_frame(
            frame=frame,
            frequency_id="1d",
            asset_identifier_by_symbol={"AAPL": "FIGI_AAPL"},
            last_update_by_asset_identifier={
                "FIGI_AAPL": dt.datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
            },
            period_cutoff=dt.datetime(2026, 4, 12, tzinfo=UTC),
        )

        self.assertEqual(list(normalized.index.names), ["time_index", "asset_identifier"])
        self.assertEqual(
            str(normalized.index.get_level_values("time_index").dtype), "datetime64[ns, UTC]"
        )
        self.assertEqual(len(normalized), 1)
        self.assertEqual(
            normalized.index[0],
            (pd.Timestamp("2026-04-11T20:00:00Z"), "FIGI_AAPL"),
        )
        self.assertEqual(float(normalized.iloc[0]["close"]), 101.6)

    def test_resolve_asset_bindings_does_not_use_figi_as_identity_fallback(self) -> None:
        class StubAsset:
            def __init__(self):
                self.unique_identifier = "ALPACA::11111111-1111-4111-8111-111111111111"
                self.figi = "BBG000BJKPG0"
                self.ticker = "FI"
                self.current_snapshot = None

        with patch(
            "src.market_data.alpaca_bars_support.build_alpaca_symbol_lookup",
            return_value={"FISV": "FISV"},
        ):
            with self.assertRaisesRegex(ValueError, "could not be resolved to Alpaca symbols"):
                resolve_asset_bindings_from_category_assets(assets=[StubAsset()])


if __name__ == "__main__":
    unittest.main()
