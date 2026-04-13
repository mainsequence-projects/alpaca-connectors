from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.run_daily_stock_bars import _parse_tickers, resolve_registered_assets_from_tickers


class RunDailyStockBarsTests(unittest.TestCase):
    def test_parse_tickers_dedupes_and_normalizes(self) -> None:
        self.assertEqual(_parse_tickers(" nvda,MSFT,nvda "), ["NVDA", "MSFT"])

    def test_resolve_registered_assets_from_tickers_requires_alpaca_match(self) -> None:
        with (
            patch("scripts.run_daily_stock_bars.fetch_alpaca_us_equities", return_value=[]),
            patch(
                "scripts.run_daily_stock_bars._resolve_requested_symbols_to_alpaca_assets",
                return_value=([], ["NVDA"], {}),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "not available in Alpaca"):
                resolve_registered_assets_from_tickers(tickers=["NVDA"])

    def test_resolve_registered_assets_from_tickers_requires_mainsequence_match(self) -> None:
        alpaca_asset = SimpleNamespace(symbol="NVDA")
        with (
            patch(
                "scripts.run_daily_stock_bars.fetch_alpaca_us_equities",
                return_value=[alpaca_asset],
            ),
            patch(
                "scripts.run_daily_stock_bars._resolve_requested_symbols_to_alpaca_assets",
                return_value=([alpaca_asset], [], {}),
            ),
            patch(
                "scripts.run_daily_stock_bars._load_mainsequence_client",
                return_value=SimpleNamespace(
                    Asset=SimpleNamespace(filter=lambda **kwargs: [])
                ),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "not registered in MainSequence"):
                resolve_registered_assets_from_tickers(tickers=["NVDA"])

    def test_resolve_registered_assets_from_tickers_returns_registered_assets(self) -> None:
        alpaca_asset = SimpleNamespace(symbol="NVDA")
        mainsequence_asset = SimpleNamespace(ticker="NVDA", unique_identifier="BBG000BBJQV0")
        with (
            patch(
                "scripts.run_daily_stock_bars.fetch_alpaca_us_equities",
                return_value=[alpaca_asset],
            ),
            patch(
                "scripts.run_daily_stock_bars._resolve_requested_symbols_to_alpaca_assets",
                return_value=([alpaca_asset], [], {}),
            ),
            patch(
                "scripts.run_daily_stock_bars._load_mainsequence_client",
                return_value=SimpleNamespace(
                    Asset=SimpleNamespace(filter=lambda **kwargs: [mainsequence_asset])
                ),
            ),
        ):
            assets, aliases = resolve_registered_assets_from_tickers(tickers=["NVDA"])

        self.assertEqual(assets, [mainsequence_asset])
        self.assertEqual(aliases, {})


if __name__ == "__main__":
    unittest.main()
