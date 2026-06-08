from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.main import main


class CliTests(unittest.TestCase):
    def test_asset_register_dry_run(self) -> None:
        plan = SimpleNamespace(
            summary=lambda: {"requested_symbols": ["AAPL", "MSFT"]},
            unresolved_symbols=[],
            warnings_by_symbol={},
        )
        resolution = SimpleNamespace(summary=lambda: {"existing_assets": ["AAPL"]})
        stdout = io.StringIO()

        with (
            patch("src.cli.asset.build_alpaca_us_equity_registration_plan", return_value=plan) as build_plan,
            patch("src.cli.asset.resolve_alpaca_us_equity_registration_plan", return_value=resolution) as resolve_plan,
            patch("src.runtime.start_markets_engine"),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["asset", "register", "--symbols", "aapl, msft"])

        self.assertEqual(exit_code, 0)
        build_plan.assert_called_once_with(
            symbols=["AAPL", "MSFT"],
            include_non_tradable=False,
            timeout=30.0,
        )
        resolve_plan.assert_called_once_with(plan, timeout=30.0)
        output = stdout.getvalue()
        self.assertIn("Planned Alpaca US equity registration", output)
        self.assertIn("Dry run only. Pass --execute to register the missing assets.", output)

    def test_asset_register_seed_tickers_expands_before_alpaca_plan(self) -> None:
        plan = SimpleNamespace(
            summary=lambda: {"requested_symbols": ["AAPL", "IVV", "MSFT"]},
            unresolved_symbols=[],
            warnings_by_symbol={},
        )
        resolution = SimpleNamespace(summary=lambda: {"existing_assets": ["AAPL"]})
        expansion_result = SimpleNamespace(
            component_provider="ishares",
            symbols_for_registration=["AAPL", "IVV", "MSFT"],
            universe=SimpleNamespace(
                seed_symbols=["IVV"],
                expanded_symbols=["AAPL", "IVV", "MSFT"],
                component_symbols_by_seed={"IVV": ["AAPL", "MSFT"]},
                unsupported_seed_symbols=[],
            ),
        )
        stdout = io.StringIO()

        with (
            patch("src.cli.asset.expand_etf_seed_symbols", return_value=expansion_result)
            as expand_seeds,
            patch("src.cli.asset.build_alpaca_us_equity_registration_plan", return_value=plan)
            as build_plan,
            patch(
                "src.cli.asset.resolve_alpaca_us_equity_registration_plan",
                return_value=resolution,
            ),
            patch("src.runtime.start_markets_engine"),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(
                [
                    "asset",
                    "register",
                    "--seed-tickers",
                    "ivv",
                    "--component-provider",
                    "ishares",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(expand_seeds.call_args.args[0].seed_tickers, ["IVV"])
        self.assertEqual(expand_seeds.call_args.args[0].component_provider, "ishares")
        build_plan.assert_called_once_with(
            symbols=["AAPL", "IVV", "MSFT"],
            include_non_tradable=False,
            timeout=30.0,
        )
        self.assertIn('"component_provider": "ishares"', stdout.getvalue())

    def test_holdings_category_create_dry_run(self) -> None:
        plan = SimpleNamespace(
            summary=lambda: {"etf_ticker": "IVV"},
            missing_registered_symbols=[],
            ambiguous_registered_symbols=[],
        )
        stdout = io.StringIO()

        with (
            patch("src.cli.holdings_category.build_holdings_asset_category_plan", return_value=plan) as build_plan,
            patch("src.runtime.start_markets_engine"),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["holdings-category", "create", "--etf-ticker", "IVV"])

        self.assertEqual(exit_code, 0)
        build_plan.assert_called_once_with(
            etf_ticker="IVV",
            component_provider=None,
            include_non_tradable=False,
            timeout=30.0,
        )
        output = stdout.getvalue()
        self.assertIn("Planned holdings AssetCategory sync", output)
        self.assertIn("Dry run only. Pass --execute to create or refresh the category.", output)

    def test_holdings_category_create_reports_ambiguous_symbols(self) -> None:
        plan = SimpleNamespace(
            summary=lambda: {"etf_ticker": "IVV"},
            etf_ticker="IVV",
            missing_registered_symbols=[],
            ambiguous_registered_symbols=["FWONK"],
            existing_asset_ids_by_symbol={"AAPL": 101},
            component_symbols=["AAPL"],
            has_blockers=lambda: False,
        )

        sync_result = SimpleNamespace(
            unique_identifier="HOLDINGS__IVV",
            display_name="HOLDINGS__IVV",
            asset_ids=[101],
        )
        stdout = io.StringIO()

        with (
            patch("src.cli.holdings_category.build_holdings_asset_category_plan", return_value=plan),
            patch("src.cli.holdings_category.sync_holdings_asset_category", return_value=sync_result)
            as sync_category,
            patch("src.runtime.start_markets_engine"),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["holdings-category", "create", "--execute", "--etf-ticker", "IVV"])

        self.assertEqual(exit_code, 0)
        sync_category.assert_called_once_with(etf_ticker="IVV", asset_ids=[101])
        output = stdout.getvalue()
        self.assertIn("These extracted component symbols resolved ambiguously in MainSequence:", output)

    def test_bars_run_plan_only(self) -> None:
        node = SimpleNamespace(
            frequency_id="1d",
            feed="iex",
            adjustment="raw",
            hash_namespace=None,
            storage_hash="storage",
            update_hash="update",
            get_table_metadata=lambda: SimpleNamespace(identifier="alpaca_stock_bars_1d_iex_raw"),
        )
        stdout = io.StringIO()

        with (
            patch(
                "src.cli.bars.build_stock_bars_node",
                return_value=(node, {"asset_count": 1, "requested_tickers": ["NVDA"]}),
            ) as build_node,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["bars", "run", "--tickers", "NVDA", "--plan-only"])

        self.assertEqual(exit_code, 0)
        build_node.assert_called_once_with(
            asset_category_unique_identifier=None,
            tickers=["NVDA"],
            frequency_id="1d",
            feed="iex",
            adjustment="raw",
            hash_namespace=None,
        )
        output = stdout.getvalue()
        self.assertIn("Planned Alpaca stock bars run", output)
        self.assertIn("Plan only. Re-run without --plan-only to execute node.run().", output)

    def test_asset_ticker_update_prices_daily_plan_only(self) -> None:
        node = SimpleNamespace(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            hash_namespace=None,
            storage_hash="storage",
            update_hash="update",
            get_table_metadata=lambda: SimpleNamespace(identifier="alpaca_stock_bars_1d_sip_all"),
        )
        stdout = io.StringIO()

        with (
            patch(
                "src.cli.bars.build_stock_bars_node",
                return_value=(
                    node,
                    {
                        "asset_count": 1,
                        "requested_tickers": ["IVV"],
                        "table_identifier": "alpaca_stock_bars_1d_sip_all",
                    },
                ),
            ) as build_node,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["asset", "IVV", "update_prices", "daily", "--plan-only"])
        self.assertEqual(exit_code, 0)
        build_node.assert_called_once_with(
            asset_category_unique_identifier=None,
            tickers=["IVV"],
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            hash_namespace=None,
        )
        output = stdout.getvalue()
        self.assertIn('"table_identifier": "alpaca_stock_bars_1d_sip_all"', output)
        self.assertIn("Planned Alpaca stock bars run", output)
        self.assertIn('"requested_period": "daily"', output)
        self.assertIn("Plan only. Re-run without --plan-only to execute node.run().", output)

    def test_bars_run_plan_only_shares_identifier_with_asset_scope(self) -> None:
        node = SimpleNamespace(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            hash_namespace=None,
            storage_hash="storage",
            update_hash="update",
            get_table_metadata=lambda: SimpleNamespace(
                identifier="alpaca_stock_bars_1d_sip_all"
            ),
        )
        stdout = io.StringIO()

        with (
            patch(
                "src.cli.bars.build_stock_bars_node",
                return_value=(
                    node,
                    {
                        "asset_count": 1,
                        "requested_tickers": ["NVDA"],
                        "table_identifier": "alpaca_stock_bars_1d_sip_all",
                    },
                ),
            ) as build_node,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main([
                "bars",
                "run",
                "--tickers",
                "NVDA",
                "--frequency-id",
                "1d",
                "--feed",
                "sip",
                "--adjustment",
                "all",
                "--plan-only",
            ])

        self.assertEqual(exit_code, 0)
        build_node.assert_called_once_with(
            asset_category_unique_identifier=None,
            tickers=["NVDA"],
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            hash_namespace=None,
        )
        output = stdout.getvalue()
        self.assertIn("Planned Alpaca stock bars run", output)
        self.assertIn('"table_identifier": "alpaca_stock_bars_1d_sip_all"', output)
        self.assertIn("Plan only. Re-run without --plan-only to execute node.run().", output)

    def test_bars_category_run_plan_only_uses_shared_identifier(self) -> None:
        node = SimpleNamespace(
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            hash_namespace=None,
            storage_hash="storage",
            update_hash="update",
            get_table_metadata=lambda: SimpleNamespace(
                identifier="alpaca_stock_bars_1d_sip_all"
            ),
        )
        stdout = io.StringIO()

        with (
            patch(
                "src.cli.bars.build_stock_bars_node",
                return_value=(
                    node,
                    {
                        "asset_count": 1,
                        "requested_tickers": ["NVDA"],
                        "table_identifier": "alpaca_stock_bars_1d_sip_all",
                    },
                ),
            ) as build_node,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main([
                "bars",
                "run",
                "--asset-category-unique-identifier",
                "HOLDINGS__IVV",
                "--frequency-id",
                "1d",
                "--feed",
                "sip",
                "--adjustment",
                "all",
                "--plan-only",
            ])

        self.assertEqual(exit_code, 0)
        build_node.assert_called_once_with(
            asset_category_unique_identifier="HOLDINGS__IVV",
            tickers=None,
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            hash_namespace=None,
        )
        output = stdout.getvalue()
        self.assertIn("Planned Alpaca stock bars run", output)
        self.assertIn('"table_identifier": "alpaca_stock_bars_1d_sip_all"', output)
        self.assertIn("Plan only. Re-run without --plan-only to execute node.run().", output)


if __name__ == "__main__":
    unittest.main()
