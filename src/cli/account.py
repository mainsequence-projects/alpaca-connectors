from __future__ import annotations

import argparse
import json
from dataclasses import asdict


def configure_register_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Register an Alpaca trading account into ms-markets and snapshot its balances + holdings. "
        "Credentials come from ALPACA_API_KEY/ALPACA_SECRET_KEY (env or MainSequence secrets); the "
        "secret is never a CLI flag."
    )
    parser.add_argument(
        "--paper",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the Alpaca paper environment (default). Pass --no-paper for the live account.",
    )
    parser.add_argument(
        "--account-name",
        default=None,
        help="Optional display name for the ms-markets Account (defaults to the unique_identifier).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional Alpaca API key override. When omitted, resolved from env/secrets.",
    )
    parser.add_argument(
        "--no-register-missing-assets",
        dest="register_missing_assets",
        action="store_false",
        default=True,
        help=(
            "Do not auto-register held equities that are not yet ms-markets assets. By default a "
            "held equity that resolves to a FIGI is registered so the account snapshot stays current; "
            "FIGI-less symbols are never created either way."
        ),
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Read the account and print the resolved plan without writing anything.",
    )
    parser.set_defaults(handler=run_account_register_command)


def run_account_register_command(args: argparse.Namespace) -> int:
    # The service functions attach the account runtime themselves (re-entrant, per process).
    if args.plan_only:
        from src.account.services import plan_alpaca_account

        summary = plan_alpaca_account(
            api_key=args.api_key,
            paper=args.paper,
            client=None,
        )
        print("Planned Alpaca account registration")
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        print("Plan only. Re-run without --plan-only to register the account.")
        return 0

    from src.account.services import register_alpaca_account

    result = register_alpaca_account(
        api_key=args.api_key,
        paper=args.paper,
        account_name=args.account_name,
        register_missing_assets=args.register_missing_assets,
    )
    print("Registered Alpaca account")
    print(json.dumps(asdict(result), indent=2, sort_keys=True, default=str))
    return 0


__all__ = ["configure_register_parser", "run_account_register_command"]
