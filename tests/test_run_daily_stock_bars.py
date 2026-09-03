from __future__ import annotations

import os
import unittest

from src.cli.bars import parse_tickers

os.environ.setdefault("MAINSEQUENCE_ACCESS_TOKEN", "dummy")
os.environ.setdefault("MAINSEQUENCE_REFRESH_TOKEN", "dummy")
os.environ["MAIN_SEQUENCE_PROJECT_ID"] = "INVALID"
os.environ.setdefault("TDAG_ROOT_PATH", "/tmp")
os.environ.setdefault("LOGGER_FILE_PATH", "/dev/stdout")

from src.market_data.alpaca_bars import AlpacaStockBarsConfig, AlpacaStockBarsNode
from src.market_data.storage import storage_for


class RunDailyStockBarsTests(unittest.TestCase):
    def test_parse_tickers_dedupes_and_normalizes(self) -> None:
        self.assertEqual(parse_tickers(" nvda,MSFT,nvda "), ["NVDA", "MSFT"])

    def test_identifier_is_shared_between_asset_and_category_scopes(self) -> None:
        ticker_config = AlpacaStockBarsConfig(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            asset_source="assets",
            asset_list=["BBG000BBJQV0"],
        )
        category_config = AlpacaStockBarsConfig(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            asset_source="universe",
            asset_category_unique_identifier="HOLDINGS__IVV",
        )
        ticker_storage = storage_for(
            ticker_config.frequency_id, ticker_config.feed, ticker_config.adjustment
        )
        category_storage = storage_for(
            category_config.frequency_id, category_config.feed, category_config.adjustment
        )
        self.assertIs(ticker_storage, category_storage)

    def test_node_requires_account_resolved_historical_client(self) -> None:
        node = object.__new__(AlpacaStockBarsNode)
        node._historical_client = None
        with self.assertRaisesRegex(RuntimeError, "account-resolved"):
            node._get_historical_client()


if __name__ == "__main__":
    unittest.main()
