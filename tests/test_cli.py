from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.main import build_parser, main


class CliTests(unittest.TestCase):
    def test_asset_register_requires_registered_account_uid(self) -> None:
        with self.assertRaises(SystemExit):
            main(["asset", "register", "--symbols", "AAPL"])

    def test_asset_register_passes_registered_account_uid_to_plan(self) -> None:
        plan = SimpleNamespace(
            openfigi_unmatched_symbols=[],
            warnings_by_symbol={},
            summary=lambda: {},
        )
        resolution = SimpleNamespace(summary=lambda: {})
        with (
            patch("src.runtime.start_markets_engine"),
            patch(
                "src.cli.asset.build_alpaca_us_equity_registration_plan",
                return_value=plan,
            ) as build_plan,
            patch(
                "src.cli.asset.resolve_alpaca_us_equity_registration_plan",
                return_value=resolution,
            ),
        ):
            exit_code = main(
                [
                    "asset",
                    "register",
                    "--account-uid",
                    "account-uid",
                    "--symbols",
                    "AAPL",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(build_plan.call_args.kwargs["account_uid"], "account-uid")

    def test_asset_register_accepts_only_exact_symbols_not_etf_seed_expansion(self) -> None:
        parser = build_parser()
        asset_parser = next(
            action.choices["asset"]
            for action in parser._actions
            if hasattr(action, "choices") and action.choices and "asset" in action.choices
        )
        register_parser = next(
            action.choices["register"]
            for action in asset_parser._actions
            if hasattr(action, "choices") and action.choices and "register" in action.choices
        )
        options = {
            option for action in register_parser._actions for option in action.option_strings
        }

        self.assertIn("--symbols", options)
        self.assertNotIn("--include-non-tradable", options)
        self.assertNotIn("--seed-tickers", options)
        self.assertNotIn("--component-provider", options)

    def test_account_help_accepts_only_secret_names(self) -> None:
        parser = build_parser()
        account_parser = next(
            action.choices["account"]
            for action in parser._actions
            if hasattr(action, "choices") and action.choices and "account" in action.choices
        )
        register_parser = next(
            action.choices["register"]
            for action in account_parser._actions
            if hasattr(action, "choices") and action.choices and "register" in action.choices
        )
        options = {
            option for action in register_parser._actions for option in action.option_strings
        }
        self.assertIn("--api-key-secret-name", options)
        self.assertIn("--secret-key-secret-name", options)
        self.assertNotIn("--api-key", options)
        self.assertNotIn("--secret-key", options)
        self.assertNotIn("--no-register-missing-assets", options)
        self.assertNotIn("--capture-initial-holdings", options)

    def test_account_register_passes_secret_names_to_shared_service(self) -> None:
        result = SimpleNamespace(
            account_uid="account-uid",
            account_unique_identifier="A__ALPACA_PAPER",
            is_paper=True,
            detail_table="AlpacaAccountDetails",
            holdings_rows=0,
            unresolved_symbols=[],
            skipped_non_equity_symbols=[],
        )
        stdout = io.StringIO()
        with (
            patch("src.account.services.register_alpaca_account", return_value=result) as register,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(
                [
                    "account",
                    "register",
                    "--api-key-secret-name",
                    "ALPACA_PAPER_API_KEY",
                    "--secret-key-secret-name",
                    "ALPACA_PAPER_SECRET_KEY",
                ]
            )
        self.assertEqual(exit_code, 0)
        register.assert_called_once_with(
            api_key_secret_name="ALPACA_PAPER_API_KEY",
            secret_key_secret_name="ALPACA_PAPER_SECRET_KEY",
            paper=True,
            account_name=None,
        )

    def test_universe_run_uses_registered_universe_uid(self) -> None:
        plan = SimpleNamespace(summary=lambda: {"etf_ticker": "IVV"}, has_blockers=lambda: False)
        universe = {
            "uid": "universe-uid",
            "source_uid": "source-uid",
            "asset_category_uid": "category-uid",
        }
        stdout = io.StringIO()
        with (
            patch("src.universes.get_asset_universe_view", return_value=universe),
            patch("src.universes.preview_asset_universe", return_value=plan) as preview,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(
                [
                    "universe",
                    "run",
                    "--universe-uid",
                    "universe-uid",
                    "--account-uid",
                    "account-uid",
                ]
            )
        self.assertEqual(exit_code, 0)
        preview.assert_called_once_with(
            "universe-uid",
            account_uid="account-uid",
            timeout=30.0,
        )
        self.assertIn("Dry run only", stdout.getvalue())

    def test_universe_source_create_calls_shared_crud(self) -> None:
        source = SimpleNamespace(
            model_dump=lambda mode=None: {
                "uid": "source-uid",
                "name": "S&P 500",
                "symbol": "IVV",
                "source_url": "https://example.com/ivv",
                "enabled": True,
            }
        )
        with patch("src.universes.create_universe_source", return_value=source) as create:
            exit_code = main(
                [
                    "universe-source",
                    "create",
                    "--name",
                    "S&P 500",
                    "--symbol",
                    "ivv",
                    "--url",
                    "https://example.com/ivv",
                ]
            )
        self.assertEqual(exit_code, 0)
        create.assert_called_once_with(
            name="S&P 500",
            symbol="ivv",
            source_url="https://example.com/ivv",
            enabled=True,
        )

    def test_asset_universe_delete_is_a_dry_run_by_default(self) -> None:
        universe = {
            "uid": "universe-uid",
            "source_uid": "source-uid",
            "asset_category_uid": "category-uid",
            "asset_count": 12,
        }
        stdout = io.StringIO()
        with (
            patch(
                "src.universes.get_asset_universe_view",
                return_value=universe,
            ),
            patch(
                "src.market_data.configurations.bar_configurations_for_universe",
                return_value=[],
            ),
            patch("src.universes.delete_asset_universe") as delete,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["universe", "delete", "universe-uid"])
        self.assertEqual(exit_code, 0)
        delete.assert_not_called()
        self.assertIn('"asset_count": 12', stdout.getvalue())

    def test_market_data_update_requires_only_stored_configuration_uid(self) -> None:
        node = SimpleNamespace()
        summary = {"dataset": {"uid": "dataset-uid"}, "account_uid": "account-uid"}
        with patch(
            "src.market_data.build_market_data_update",
            return_value=(node, summary),
        ) as build:
            exit_code = main(
                [
                    "market-data",
                    "update",
                    "--configuration-uid",
                    "configuration-uid",
                ]
            )
        self.assertEqual(exit_code, 0)
        build.assert_called_once_with(
            configuration_uid="configuration-uid",
            hash_namespace=None,
        )

    def test_bar_configuration_create_calls_shared_crud(self) -> None:
        row = SimpleNamespace(
            model_dump=lambda mode=None: {"uid": "configuration-uid", "asset_source": "assets"}
        )
        with patch("src.market_data.create_bar_configuration", return_value=row) as create:
            exit_code = main(
                [
                    "market-data",
                    "bar-configuration",
                    "create",
                    "--name",
                    "Daily holdings",
                    "--account-uid",
                    "account-uid",
                    "--asset-source",
                    "assets",
                    "--asset-uids",
                    "asset-1,asset-2",
                    "--frequency",
                    "1d",
                    "--feed",
                    "sip",
                    "--adjustment",
                    "all",
                ]
            )
        self.assertEqual(exit_code, 0)
        create.assert_called_once_with(
            name="Daily holdings",
            description=None,
            account_uid="account-uid",
            asset_source="assets",
            asset_uids=["asset-1", "asset-2"],
            universe_uid=None,
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            enabled=True,
        )

    def test_signal_create_forwards_durable_job_configuration(self) -> None:
        row = SimpleNamespace(
            model_dump=lambda mode=None: {
                "uid": "configuration-uid",
                "job_uid": "job-uid",
            }
        )
        with (
            patch(
                "src.operations.create_signal_job_configuration",
                return_value=row,
            ) as create,
            patch(
                "src.operations.signal_uid_for_configuration",
                return_value="signal-uid",
            ),
        ):
            exit_code = main(
                [
                    "signal",
                    "create",
                    "--name",
                    "Daily IVV observation",
                    "--universe-uid",
                    "universe-uid",
                    "--account-uid",
                    "account-uid",
                    "--schedule-type",
                    "interval",
                    "--schedule-every",
                    "1",
                    "--schedule-period",
                    "days",
                ]
            )

        self.assertEqual(exit_code, 0)
        create.assert_called_once_with(
            name="Daily IVV observation",
            description=None,
            universe_uid="universe-uid",
            account_uid="account-uid",
            enabled=True,
            schedule_type="interval",
            schedule_every=1,
            schedule_period="days",
            schedule_expression=None,
            schedule_timezone=None,
            schedule_start_time=None,
            cpu_request="0.25",
            memory_request="0.5",
            max_runtime_seconds=3600,
            spot=False,
        )

    def test_signal_create_does_not_expose_an_environment_selector(self) -> None:
        with self.assertRaises(SystemExit):
            main(
                [
                    "signal",
                    "create",
                    "--name",
                    "Daily IVV observation",
                    "--universe-uid",
                    "universe-uid",
                    "--account-uid",
                    "account-uid",
                    "--schedule-type",
                    "interval",
                    "--schedule-every",
                    "1",
                    "--schedule-period",
                    "days",
                    "--environment-uid",
                    "environment-uid",
                ]
            )

    def test_portfolio_prepare_interpolated_prices_uses_migration_preflight(self) -> None:
        result = {
            "created_revision": False,
            "dynamic_provider": "src.portfolios.interpolated_prices_migration:migration",
            "storages": [],
        }
        with patch(
            "src.portfolios.interpolated_prices_schema.prepare_interpolated_prices_schema",
            return_value=result,
        ) as prepare:
            exit_code = main(
                ["portfolio", "prepare-interpolated-prices", "--check-only"]
            )

        self.assertEqual(exit_code, 0)
        prepare.assert_called_once_with(check_only=True, revision_message=None)

    def test_unexpected_provider_error_is_sanitized(self) -> None:
        stderr = io.StringIO()
        sensitive = "raw-secret-value"
        with (
            patch(
                "src.universes.preview_universe_source",
                side_effect=RuntimeError(f"upstream headers contained {sensitive}"),
            ),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main(["universe-source", "preview", "source-uid"])

        self.assertEqual(exit_code, 2)
        self.assertNotIn(sensitive, stderr.getvalue())
        self.assertIn("dependency_unavailable", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
