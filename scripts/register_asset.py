from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.assets import (
    build_alpaca_us_equity_registration_plan,
    register_alpaca_us_equity_assets,
    resolve_alpaca_us_equity_registration_plan,
)
from src.settings import SUPPORTED_COMPONENT_PROVIDERS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Register Alpaca US equity assets as MainSequence public assets using "
            "OpenFIGI classification."
        ),
    )
    symbol_group = parser.add_mutually_exclusive_group()
    symbol_group.add_argument(
        "--symbols",
        help="Comma-separated list of exact symbols to register. No component extraction is performed.",
    )
    symbol_group.add_argument(
        "--seed-tickers",
        help="Comma-separated list of seed tickers for component extraction.",
    )
    parser.add_argument(
        "--component-provider",
        choices=list(SUPPORTED_COMPONENT_PROVIDERS),
        help="Component extraction provider. Required when --seed-tickers is used.",
    )
    parser.add_argument(
        "--include-non-tradable",
        action="store_true",
        help="Include active Alpaca assets that are not tradable.",
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
        help="HTTP timeout in seconds for OpenFIGI and MainSequence requests.",
    )
    return parser


def _parse_symbols(raw_symbols: str | None) -> list[str] | None:
    if not raw_symbols:
        return None
    symbols = [symbol.strip().upper() for symbol in raw_symbols.split(",") if symbol.strip()]
    return symbols or None


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.seed_tickers and not args.component_provider:
        parser.error("--component-provider is required when --seed-tickers is used.")
    if args.component_provider and not args.seed_tickers:
        parser.error("--seed-tickers is required when --component-provider is used.")

    plan = build_alpaca_us_equity_registration_plan(
        symbols=_parse_symbols(args.symbols),
        seed_tickers=_parse_symbols(args.seed_tickers),
        component_provider=args.component_provider,
        include_non_tradable=args.include_non_tradable,
        timeout=args.timeout,
    )
    resolution = resolve_alpaca_us_equity_registration_plan(
        plan,
        timeout=args.timeout,
    )

    print("Planned Alpaca US equity registration")
    print(json.dumps({**plan.summary(), **resolution.summary()}, indent=2, sort_keys=True))

    if plan.unresolved_symbols:
        print(
            "Strict FIGI filter: these symbols were not eligible for registration because no FIGI was found:"
        )
        for symbol in plan.unresolved_symbols:
            warning = plan.warnings_by_symbol.get(symbol)
            if warning:
                print(f"  - {symbol}: {warning}")
            else:
                print(f"  - {symbol}")

    if not args.execute:
        print("Dry run only. Pass --execute to register the missing assets.")
        return

    print("Executing registration against MainSequence...")
    results = register_alpaca_us_equity_assets(
        registration_resolution=resolution,
        timeout=args.timeout,
    )
    print(
        json.dumps(
            {
                "resolved_assets": {
                    symbol: getattr(asset, "id", asset)
                    for symbol, asset in results["assets"].items()
                },
                "existing_assets": results["existing_assets"],
                "created_assets": {
                    symbol: asset.id for symbol, asset in results["created_assets"].items()
                },
                "unresolved_symbols": results["unresolved_symbols"],
                "not_registered_missing_figi_symbols": results["not_registered_missing_figi_symbols"],
                "not_registered_missing_alpaca_symbols": results["not_registered_missing_alpaca_symbols"],
                "warnings_by_symbol": results["warnings_by_symbol"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
