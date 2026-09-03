from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.main import build_parser, main


class CliTests(unittest.TestCase):
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
            capture_initial_holdings=False,
            register_missing_assets=True,
        )

    def test_universe_sync_uses_source_uid_not_ticker_provider_pair(self) -> None:
        plan = SimpleNamespace(summary=lambda: {"etf_ticker": "IVV"}, has_blockers=lambda: False)
        stdout = io.StringIO()
        with (
            patch("src.universes.preview_universe_source", return_value=plan) as preview,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(["universe", "sync", "--source-uid", "source-uid"])
        self.assertEqual(exit_code, 0)
        preview.assert_called_once_with("source-uid", timeout=30.0)
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

    def test_materialized_universe_delete_is_a_dry_run_by_default(self) -> None:
        universe = {"uid": "universe-uid", "asset_count": 12}
        stdout = io.StringIO()
        with (
            patch(
                "src.universes.get_materialized_universe",
                return_value=universe,
            ),
            patch("src.universes.delete_materialized_universe") as delete,
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
