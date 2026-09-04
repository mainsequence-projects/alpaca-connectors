from __future__ import annotations

import argparse
import json

from src.assets import (
    build_alpaca_us_equity_registration_plan,
    register_alpaca_us_equity_assets,
    resolve_alpaca_us_equity_registration_plan,
)


def configure_register_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Register Alpaca US equity assets with Alpaca's immutable asset UUID as identity. "
        "OpenFIGI metadata is optional enrichment."
    )
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated list of exact Alpaca symbols to register.",
    )
    parser.add_argument(
        "--account-uid",
        required=True,
        help=(
            "Registered Alpaca Account UID whose stored Main Sequence Secret references are used "
            "for provider access."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the MainSequence registration after printing the plan.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds for provider and Main Sequence requests.",
    )
    parser.set_defaults(handler=run_register_command)


def _parse_symbols(raw_symbols: str | None) -> list[str] | None:
    if not raw_symbols:
        return None
    symbols = [symbol.strip().upper() for symbol in raw_symbols.split(",") if symbol.strip()]
    return symbols or None


def run_register_command(args: argparse.Namespace) -> int:
    symbols = _parse_symbols(args.symbols)
    if not symbols:
        raise SystemExit("--symbols must contain at least one exact Alpaca symbol.")

    # Resolving existing assets + executing registration go through ms-markets MetaTables.
    from src.runtime import start_markets_engine

    start_markets_engine()

    plan = build_alpaca_us_equity_registration_plan(
        account_uid=args.account_uid,
        symbols=symbols,
        timeout=args.timeout,
    )
    resolution = resolve_alpaca_us_equity_registration_plan(
        plan,
        timeout=args.timeout,
    )

    print("Planned Alpaca US equity registration")
    summary = {**plan.summary(), **resolution.summary()}
    print(json.dumps(summary, indent=2, sort_keys=True))

    if plan.openfigi_unmatched_symbols:
        print("Optional OpenFIGI enrichment was unavailable for these registered candidates:")
        for symbol in plan.openfigi_unmatched_symbols:
            warning = plan.warnings_by_symbol.get(symbol)
            if warning:
                print(f"  - {symbol}: {warning}")
            else:
                print(f"  - {symbol}")

    if not args.execute:
        print("Dry run only. Pass --execute to register the missing assets.")
        return 0

    print("Executing registration against MainSequence...")
    results = register_alpaca_us_equity_assets(
        registration_resolution=resolution,
        timeout=args.timeout,
    )
    print(
        json.dumps(
            {
                # Values are ms-markets asset uid strings (JSON-safe).
                "resolved_assets": results["assets"],
                "existing_assets": results["existing_assets"],
                "created_assets": results["created_assets"],
                "openfigi_unmatched_symbols": results["openfigi_unmatched_symbols"],
                "not_registered_missing_alpaca_symbols": results[
                    "not_registered_missing_alpaca_symbols"
                ],
                "warnings_by_symbol": results["warnings_by_symbol"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0
