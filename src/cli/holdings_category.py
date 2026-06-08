from __future__ import annotations

import argparse
import json

from etf_extraction.holdings_categories import (
    build_holdings_asset_category_plan,
    sync_holdings_asset_category,
)
from etf_extraction.settings import SUPPORTED_COMPONENT_PROVIDERS


def configure_create_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Create or refresh a MainSequence AssetCategory from an ETF holdings extraction path."
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
        help="Accepted for CLI compatibility; ETF category sync no longer depends on Alpaca validation.",
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
    parser.set_defaults(handler=run_create_command)


def run_create_command(args: argparse.Namespace) -> int:
    # Resolving registered assets + writing the category go through ms-markets MetaTables.
    from src.runtime import start_markets_engine

    start_markets_engine()

    plan = build_holdings_asset_category_plan(
        etf_ticker=args.etf_ticker,
        component_provider=args.component_provider,
        include_non_tradable=args.include_non_tradable,
        timeout=args.timeout,
    )

    print("Planned holdings AssetCategory sync")
    print(json.dumps(plan.summary(), indent=2, sort_keys=True))

    if plan.missing_registered_symbols:
        print("These extracted component symbols are not yet registered as MainSequence assets:")
        for symbol in plan.missing_registered_symbols:
            print(f"  - {symbol}")

    if plan.ambiguous_registered_symbols:
        print("These extracted component symbols resolved ambiguously in MainSequence:")
        for symbol in plan.ambiguous_registered_symbols:
            print(f"  - {symbol}")

    if not args.execute:
        print("Dry run only. Pass --execute to create or refresh the category.")
        return 0

    if plan.has_blockers():
        raise SystemExit(
            "Refusing to create the holdings category because extracted holdings are incomplete "
            "or not fully registered uniquely in MainSequence."
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
    return 0
