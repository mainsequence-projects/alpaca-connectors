from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from src.cli.bars import parse_tickers

os.environ.setdefault("MAINSEQUENCE_ACCESS_TOKEN", "dummy")
os.environ.setdefault("MAINSEQUENCE_REFRESH_TOKEN", "dummy")
os.environ["MAIN_SEQUENCE_PROJECT_ID"] = "INVALID"
os.environ.setdefault("TDAG_ROOT_PATH", "/tmp")
os.environ.setdefault("LOGGER_FILE_PATH", "/dev/stdout")

from src.market_data.alpaca_bars import AlpacaStockBarsConfig, AlpacaStockBarsNode
from src.market_data.alpaca_bars_support import AlpacaBarAssetBinding
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

    def test_daily_update_requests_only_finalized_periods(self) -> None:
        identifier = "ALPACA::11111111-1111-4111-8111-111111111111"
        node = object.__new__(AlpacaStockBarsNode)
        node.config = SimpleNamespace(frequency_id="1d", feed="sip", adjustment="all")
        node._historical_client = object()
        node._asset_bindings = [
            AlpacaBarAssetBinding(
                asset=object(),
                unique_identifier=identifier,
                alpaca_symbol="AAPL",
            )
        ]
        offset_start = pd.Timestamp("2026-09-01T00:00:00Z").to_pydatetime()

        with (
            patch.object(AlpacaStockBarsNode, "get_offset_start", return_value=offset_start),
            patch.object(
                AlpacaStockBarsNode,
                "get_asset_update_range_map_great_or_equal",
                return_value={},
            ),
            patch(
                "src.market_data.alpaca_bars.fetch_stock_bars_frame",
                return_value=pd.DataFrame(),
            ) as fetch_bars,
        ):
            result = node.update()

        assert result.empty
        requested_end = fetch_bars.call_args.kwargs["end"]
        assert requested_end.hour == 0
        assert requested_end.minute == 0
        assert requested_end.second == 0
        assert requested_end.microsecond == 0


if __name__ == "__main__":
    unittest.main()
