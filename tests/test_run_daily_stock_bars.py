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

from src.data_nodes.alpaca_bars import AlpacaStockBarsConfig, AlpacaStockBarsNode


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
            patch(
                "src.cli.bars._load_mainsequence_client",
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
                "src.cli.bars.fetch_alpaca_us_equities",
                return_value=[alpaca_asset],
            ),
            patch(
                "src.cli.bars._resolve_requested_symbols_to_alpaca_assets",
                return_value=([alpaca_asset], [], {}),
            ),
            patch(
                "src.cli.bars._load_mainsequence_client",
                return_value=SimpleNamespace(
                    Asset=SimpleNamespace(filter=lambda **kwargs: [mainsequence_asset])
                ),
            ),
        ):
            assets, aliases = resolve_registered_assets_from_tickers(tickers=["NVDA"])

        self.assertEqual(assets, [mainsequence_asset])
        self.assertEqual(aliases, {})

    def test_identifier_is_shared_between_ticker_and_category_scopes(self) -> None:
        def fake_data_node_init(self, config=None, *args, **kwargs):
            self.config = config
            self._framework_initialized = True

        def fake_initialize_configuration(self, init_kwargs):
            self.storage_hash = init_kwargs.get("storage_hash", "storage")
            self.update_hash = init_kwargs.get("update_hash", "update")

        def fake_set_data_source(self, data_source=None) -> None:
            self._data_source = data_source

        ticker_config = AlpacaStockBarsConfig(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            asset_list=[],
        )
        with (
            patch("src.data_nodes.alpaca_bars.DataNode.__init__", new=fake_data_node_init),
            patch(
                "src.data_nodes.alpaca_bars.DataNode._initialize_configuration",
                new=fake_initialize_configuration,
            ),
            patch(
                "src.data_nodes.alpaca_bars.DataNode.set_data_source",
                new=fake_set_data_source,
            ),
        ):
            AlpacaStockBarsNode(config=ticker_config)
        ticker_identifier = ticker_config.node_metadata.identifier

        category_config = AlpacaStockBarsConfig(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            asset_category_unique_identifier="HOLDINGS__IVV",
        )
        with (
            patch("src.data_nodes.alpaca_bars.DataNode.__init__", new=fake_data_node_init),
            patch(
                "src.data_nodes.alpaca_bars.DataNode._initialize_configuration",
                new=fake_initialize_configuration,
            ),
            patch(
                "src.data_nodes.alpaca_bars.DataNode.set_data_source",
                new=fake_set_data_source,
            ),
        ):
            AlpacaStockBarsNode(config=category_config)
        category_identifier = category_config.node_metadata.identifier

        self.assertEqual(
            ticker_identifier,
            category_identifier,
            "Ticker and category scopes must resolve to same dataset id.",
        )
        self.assertEqual(ticker_identifier, "alpaca_stock_bars_1d_sip_all")


if __name__ == "__main__":
    unittest.main()
