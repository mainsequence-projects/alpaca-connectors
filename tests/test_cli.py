from __future__ import annotations

import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from alpaca_connectors.cli.main import build_parser, main


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
            patch("alpaca_connectors.runtime.start_markets_engine"),
            patch(
                "alpaca_connectors.cli.asset.build_alpaca_us_equity_registration_plan",
                return_value=plan,
            ) as build_plan,
            patch(
                "alpaca_connectors.cli.asset.resolve_alpaca_us_equity_registration_plan",
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

    def test_asset_register_accepts_only_exact_symbols_without_seed_expansion(self) -> None:
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
            patch(
                "alpaca_connectors.account.services.register_alpaca_account", return_value=result
            ) as register,
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
        from alpaca_connectors.account.credentials import AlpacaSecretNames

        register.assert_called_once_with(
            credentials=AlpacaSecretNames(
                api_key_secret_name="ALPACA_PAPER_API_KEY",
                secret_key_secret_name="ALPACA_PAPER_SECRET_KEY",
            ),
            paper=True,
            account_name=None,
        )

    def test_account_update_imports_keys_only_from_both_secret_names(self) -> None:
        from alpaca_connectors.account.credentials import AlpacaSecretNames

        with patch(
            "alpaca_connectors.account.services.update_account_registration",
            return_value={"account_uid": "account-uid"},
        ) as update:
            with self.assertRaises(SystemExit):
                main(["account", "update", "account-uid", "--api-key-secret-name", "ONLY_ONE"])
            update.assert_not_called()
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "account",
                        "update",
                        "account-uid",
                        "--api-key-secret-name",
                        "NEW_API_KEY",
                        "--secret-key-secret-name",
                        "NEW_SECRET_KEY",
                    ]
                )

        self.assertEqual(exit_code, 0)
        update.assert_called_once_with(
            "account-uid",
            account_name=None,
            credential_secret_names=AlpacaSecretNames("NEW_API_KEY", "NEW_SECRET_KEY"),
            account_is_active=None,
        )

    def test_only_the_provided_weights_portfolio_commands_remain(self) -> None:
        # ADR 0027: one kind of portfolio, from provided weights; no universe, source or signal.
        parser = build_parser()
        top_level = next(
            action.choices for action in parser._actions if getattr(action, "choices", None)
        )
        self.assertEqual(
            set(top_level),
            {"asset", "market-data", "account", "holdings", "portfolio"},
        )
        portfolio_commands = next(
            action.choices
            for action in top_level["portfolio"]._actions
            if getattr(action, "choices", None)
        )
        # ADR 0028 adds the `adopt` rollout; still no command creates a portfolio or gives weights.
        # ADR 0029 removes `prepare-interpolated-prices`: the migration Job creates those tables.
        self.assertEqual(
            set(portfolio_commands),
            {"list", "get", "delete", "run", "runs", "rebalance", "adopt"},
        )
        for command in (
            ["universe-source", "list"],
            ["universe", "list"],
            ["signal", "list"],
            ["portfolio", "create", "--name", "P", "--signal-configuration-uid", "s"],
        ):
            with (
                self.subTest(command=command[:2]),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                main(command)

    def test_market_data_update_requires_only_stored_configuration_uid(self) -> None:
        node = SimpleNamespace()
        summary = {"dataset": {"uid": "dataset-uid"}, "account_uid": "account-uid"}
        with patch(
            "alpaca_connectors.market_data.build_market_data_update",
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
        with patch(
            "alpaca_connectors.market_data.create_bar_configuration", return_value=row
        ) as create:
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
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            enabled=True,
            scheduled=True,
        )

    def test_bar_configuration_update_can_leave_the_daily_run(self) -> None:
        row = SimpleNamespace(
            model_dump=lambda mode=None: {"uid": "configuration-uid", "scheduled": False}
        )
        with (
            patch(
                "alpaca_connectors.market_data.update_bar_configuration", return_value=row
            ) as update,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exit_code = main(
                [
                    "market-data",
                    "bar-configuration",
                    "update",
                    "configuration-uid",
                    "--no-scheduled",
                ]
            )

        self.assertEqual(exit_code, 0)
        update.assert_called_once_with("configuration-uid", scheduled=False)

    def test_portfolio_delete_retires_the_portfolio_then_deletes_the_configuration(self) -> None:
        row = SimpleNamespace(uid="configuration-uid")
        removal = SimpleNamespace(
            summary=lambda: {
                "configuration_uid": "configuration-uid",
                "deleted": True,
                "portfolio": {"uid": "portfolio-uid", "state": "retired"},
            }
        )
        stdout = io.StringIO()
        with (
            patch("alpaca_connectors.portfolios.get_portfolio_configuration", return_value=row),
            patch(
                "alpaca_connectors.portfolios.provided_weights.end_portfolio_configuration",
                return_value=removal,
            ) as end,
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(main(["portfolio", "delete", "configuration-uid"]), 0)
            end.assert_not_called()
            self.assertIn("will be retired", json.loads(stdout.getvalue())["detail"])
            stdout.truncate(0)
            stdout.seek(0)

            self.assertEqual(main(["portfolio", "delete", "configuration-uid", "--execute"]), 0)

        end.assert_called_once_with(row)
        self.assertEqual(json.loads(stdout.getvalue())["portfolio"]["state"], "retired")

    def test_portfolio_delete_reports_a_portfolio_an_account_targets(self) -> None:
        from alpaca_connectors.portfolios.provided_weights import ProvidedWeightsError

        refusal = ProvidedWeightsError(
            "portfolio_in_account_target", "An account's current target names it.", status_code=409
        )
        stderr = io.StringIO()
        with (
            patch(
                "alpaca_connectors.portfolios.get_portfolio_configuration",
                return_value=SimpleNamespace(uid="configuration-uid"),
            ),
            patch(
                "alpaca_connectors.portfolios.provided_weights.end_portfolio_configuration",
                side_effect=refusal,
            ),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main(["portfolio", "delete", "configuration-uid", "--execute"])

        self.assertEqual(exit_code, 2)
        error = json.loads(stderr.getvalue())["error"]
        self.assertEqual(error["code"], "action_blocked")
        self.assertIn("current target", error["message"])

    def test_unexpected_provider_error_is_sanitized(self) -> None:
        stderr = io.StringIO()
        sensitive = "raw-secret-value"
        with (
            patch(
                "alpaca_connectors.portfolios.get_portfolio_configuration",
                side_effect=RuntimeError(f"upstream headers contained {sensitive}"),
            ),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main(["portfolio", "get", "configuration-uid"])

        self.assertEqual(exit_code, 2)
        self.assertNotIn(sensitive, stderr.getvalue())
        self.assertIn("dependency_unavailable", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
