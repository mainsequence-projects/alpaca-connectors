from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.holdings_categories import (
    build_holdings_asset_category_plan,
    sync_holdings_asset_category,
)
from src.settings import SUPPORTED_COMPONENT_PROVIDERS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create or refresh a MainSequence AssetCategory from an ETF holdings extraction path."
        ),
    )
    parser.add_argument(
        "--etf-ticker",
        required=True,
        help="ETF seed ticker, for example IVV, QQQ, VNQ, or SPY.",
    )
    parser.add_argument(
        "--component-provider",
        choices=list(SUPPORTED_COMPONENT_PROVIDERS),
        help="Optional component provider override. When omitted, the provider is inferred from settings.",
    )
    parser.add_argument(
        "--include-non-tradable",
        action="store_true",
        help="Include active Alpaca assets that are not tradable when checking holdings membership.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the category to MainSequence after the strict validation passes.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds for extraction, Alpaca, and MainSequence requests.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    plan = build_holdings_asset_category_plan(
        etf_ticker=args.etf_ticker,
        component_provider=args.component_provider,
        include_non_tradable=args.include_non_tradable,
        timeout=args.timeout,
    )

    print("Planned holdings AssetCategory sync")
    print(json.dumps(plan.summary(), indent=2, sort_keys=True))

    if plan.registration_plan.missing_symbols_from_alpaca:
        print("These extracted component symbols do not exist in Alpaca and will block category creation:")
        for symbol in plan.registration_plan.missing_symbols_from_alpaca:
            print(f"  - {symbol}")

    if plan.registration_plan.unresolved_symbols:
        print("These extracted component symbols have no FIGI match and will block category creation:")
        for symbol in plan.registration_plan.unresolved_symbols:
            warning = plan.registration_plan.warnings_by_symbol.get(symbol)
            if warning:
                print(f"  - {symbol}: {warning}")
            else:
                print(f"  - {symbol}")

    if plan.missing_registered_symbols:
        print("These extracted component symbols are not yet registered as MainSequence assets:")
        for symbol in plan.missing_registered_symbols:
            print(f"  - {symbol}")

    if not args.execute:
        print("Dry run only. Pass --execute to create or refresh the category.")
        return

    if plan.has_blockers():
        raise SystemExit(
            "Refusing to create the holdings category because extracted holdings are incomplete "
            "or not fully registered in MainSequence."
        )

    result = sync_holdings_asset_category(
        etf_ticker=plan.etf_ticker,
        asset_ids=[
            plan.existing_asset_ids_by_symbol[symbol]
            for symbol in plan.component_symbols
            if symbol in plan.existing_asset_ids_by_symbol
        ],
    )
    print("Created or refreshed holdings AssetCategory")
    print(
        json.dumps(
            {
                "unique_identifier": result.unique_identifier,
                "display_name": result.display_name,
                "asset_ids": result.asset_ids,
                "asset_count": len(result.asset_ids),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
