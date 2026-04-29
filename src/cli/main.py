from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from .asset import configure_register_parser
from .bars import configure_run_parser, run_asset_price_update_command
from .holdings_category import configure_create_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alpaca-connectors",
        description="Project CLI for Alpaca connector asset and holdings workflows.",
    )
    top_level = parser.add_subparsers(dest="resource")

    asset_parser = top_level.add_parser(
        "asset",
        help="Asset commands, including registration and ticker-scoped price updates.",
    )
    asset_commands = asset_parser.add_subparsers(dest="asset_command")
    configure_register_parser(
        asset_commands.add_parser("register", help="Register Alpaca US equity assets.")
    )

    bars_parser = top_level.add_parser(
        "bars",
        help="Generic Alpaca stock bar update commands.",
    )
    bars_commands = bars_parser.add_subparsers(dest="bars_command")
    configure_run_parser(
        bars_commands.add_parser("run", help="Build and run the Alpaca stock bars DataNode.")
    )

    holdings_parser = top_level.add_parser(
        "holdings-category",
        help="AssetCategory commands driven by ETF holdings.",
    )
    holdings_commands = holdings_parser.add_subparsers(dest="holdings_command")
    configure_create_parser(
        holdings_commands.add_parser(
            "create",
            help="Create or refresh a holdings AssetCategory.",
        )
    )
    return parser


def _looks_like_asset_price_update_command(argv: Sequence[str]) -> bool:
    return len(argv) >= 2 and argv[0] == "asset" and argv[1] not in {"register", "-h", "--help"}


def main(argv: Sequence[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    if argv_list and _looks_like_asset_price_update_command(argv_list):
        return run_asset_price_update_command(argv_list[1:])

    parser = build_parser()
    args = parser.parse_args(argv_list)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)
