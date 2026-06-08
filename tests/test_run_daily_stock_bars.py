from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.bars import parse_tickers, resolve_registered_assets_from_tickers

os.environ.setdefault("MAINSEQUENCE_ACCESS_TOKEN", "dummy")
os.environ.setdefault("MAINSEQUENCE_REFRESH_TOKEN", "dummy")
os.environ["MAIN_SEQUENCE_PROJECT_ID"] = "INVALID"
os.environ.setdefault("TDAG_ROOT_PATH", "/tmp")
os.environ.setdefault("LOGGER_FILE_PATH", "/dev/stdout")

from src.data_nodes.alpaca_bars import AlpacaStockBarsConfig
from src.markets_storage.alpaca_bars import storage_for


class RunDailyStockBarsTests(unittest.TestCase):
    def test_parse_tickers_dedupes_and_normalizes(self) -> None:
        self.assertEqual(parse_tickers(" nvda,MSFT,nvda "), ["NVDA", "MSFT"])

    def test_resolve_registered_assets_from_tickers_requires_alpaca_match(self) -> None:
        with (
            patch("src.cli.bars.fetch_alpaca_us_equities", return_value=[]),
            patch(
                "src.cli.bars._resolve_requested_symbols_to_alpaca_assets",
                return_value=([], ["NVDA"], {}),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "not available in Alpaca"):
                resolve_registered_assets_from_tickers(tickers=["NVDA"])

    def test_resolve_registered_assets_from_tickers_requires_mainsequence_match(self) -> None:
        alpaca_asset = SimpleNamespace(symbol="NVDA")
        with (
            patch(
                "src.cli.bars.fetch_alpaca_us_equities",
                return_value=[alpaca_asset],
            ),
            patch(
                "src.cli.bars._resolve_requested_symbols_to_alpaca_assets",
                return_value=([alpaca_asset], [], {}),
            ),
            # No OpenFigiDetails-backed asset matches the ticker -> not registered.
            patch("src.assets.resolution.assets_for_ticker", return_value=[]),
        ):
            with self.assertRaisesRegex(RuntimeError, "not registered in MainSequence"):
                resolve_registered_assets_from_tickers(tickers=["NVDA"])

    def test_resolve_registered_assets_from_tickers_returns_unique_identifiers(self) -> None:
        alpaca_asset = SimpleNamespace(symbol="NVDA")
        mainsequence_asset = SimpleNamespace(unique_identifier="BBG000BBJQV0")
        with (
            patch(
                "src.cli.bars.fetch_alpaca_us_equities",
                return_value=[alpaca_asset],
            ),
            patch(
                "src.cli.bars._resolve_requested_symbols_to_alpaca_assets",
                return_value=([alpaca_asset], [], {}),
            ),
            patch(
                "src.assets.resolution.assets_for_ticker",
                return_value=[mainsequence_asset],
            ),
        ):
            unique_identifiers, aliases = resolve_registered_assets_from_tickers(tickers=["NVDA"])

        # asset_list is now a list of asset unique-identifier strings (not asset objects).
        self.assertEqual(unique_identifiers, ["BBG000BBJQV0"])
        self.assertEqual(aliases, {})

    def test_identifier_is_shared_between_ticker_and_category_scopes(self) -> None:
        # In the storage-first architecture the published dataset identity is determined by the
        # storage table selected from (frequency_id, feed, adjustment) -- NOT by the asset scope.
        # So a ticker-scoped and a category-scoped config for 1d/sip/all bind the SAME storage
        # class and therefore the same identifier `alpaca_stock_bars_1d_sip_all`.
        ticker_config = AlpacaStockBarsConfig(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            asset_list=["BBG000BBJQV0"],
        )
        category_config = AlpacaStockBarsConfig(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            asset_category_unique_identifier="HOLDINGS__IVV",
        )

        ticker_storage = storage_for(
            ticker_config.frequency_id, ticker_config.feed, ticker_config.adjustment
        )
        category_storage = storage_for(
            category_config.frequency_id, category_config.feed, category_config.adjustment
        )

        self.assertIs(
            ticker_storage,
            category_storage,
            "Ticker and category scopes must resolve to the same dataset/storage table.",
        )
        self.assertEqual(
            ticker_storage.__metatable_identifier__, "alpaca_stock_bars_1d_sip_all"
        )


if __name__ == "__main__":
    unittest.main()
