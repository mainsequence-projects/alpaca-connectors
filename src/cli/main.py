from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .account import (
    configure_get_parser as configure_account_get_parser,
)
from .account import (
    configure_list_parser as configure_account_list_parser,
)
from .account import (
    configure_refresh_parser as configure_account_refresh_parser,
)
from .account import (
    configure_register_parser as configure_account_register_parser,
)
from .account import (
    configure_remove_parser as configure_account_remove_parser,
)
from .account import (
    configure_update_parser as configure_account_update_parser,
)
from .asset import configure_register_parser
from .bars import (
    configure_configuration_create_parser,
    configure_configuration_delete_parser,
    configure_configuration_get_parser,
    configure_configuration_list_parser,
    configure_configuration_update_parser,
    configure_dataset_get_parser,
    configure_dataset_list_parser,
    configure_prices_parser,
    configure_run_parser,
    run_asset_price_update_command,
)
from .holdings import (
    configure_capture_parser as configure_holdings_capture_parser,
)
from .holdings import (
    configure_list_parser as configure_holdings_list_parser,
)
from .holdings_category import (
    configure_delete_parser as configure_universe_delete_parser,
)
from .holdings_category import configure_get_parser as configure_universe_get_parser
from .holdings_category import configure_list_parser as configure_universe_list_parser
from .holdings_category import configure_sync_parser
from .universe_source import (
    configure_create_parser as configure_source_create_parser,
)
from .universe_source import (
    configure_delete_parser as configure_source_delete_parser,
)
from .universe_source import (
    configure_get_parser as configure_source_get_parser,
)
from .universe_source import (
    configure_list_parser as configure_source_list_parser,
)
from .universe_source import (
    configure_preview_parser as configure_source_preview_parser,
)
from .universe_source import (
    configure_seed_parser as configure_source_seed_parser,
)
from .universe_source import (
    configure_update_parser as configure_source_update_parser,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alpaca-connectors",
        description="Capability-oriented CLI for the Alpaca Connectors project.",
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

    market_data_parser = top_level.add_parser(
        "market-data",
        help="Market Data capability commands.",
    )
    market_data_commands = market_data_parser.add_subparsers(dest="market_data_command")
    configure_run_parser(
        market_data_commands.add_parser(
            "update",
            help="Plan or execute an Alpaca stock-bar update.",
        )
    )
    configuration_parser = market_data_commands.add_parser(
        "bar-configuration",
        help="Create, review, change, and remove stored bar configurations.",
    )
    configuration_commands = configuration_parser.add_subparsers(dest="bar_configuration_command")
    configure_configuration_list_parser(configuration_commands.add_parser("list"))
    configure_configuration_get_parser(configuration_commands.add_parser("get"))
    configure_configuration_create_parser(configuration_commands.add_parser("create"))
    configure_configuration_update_parser(configuration_commands.add_parser("update"))
    configure_configuration_delete_parser(configuration_commands.add_parser("delete"))
    dataset_parser = market_data_commands.add_parser(
        "dataset", help="Inspect migrated price datasets."
    )
    dataset_commands = dataset_parser.add_subparsers(dest="dataset_command")
    configure_dataset_list_parser(dataset_commands.add_parser("list"))
    configure_dataset_get_parser(dataset_commands.add_parser("get"))
    configure_prices_parser(
        market_data_commands.add_parser("prices", help="Query bounded price observations.")
    )

    account_parser = top_level.add_parser(
        "account",
        help="Account commands: register an Alpaca trading account into ms-markets.",
    )
    account_commands = account_parser.add_subparsers(dest="account_command")
    configure_account_register_parser(
        account_commands.add_parser(
            "register",
            help="Register an Alpaca account and snapshot its balances + holdings.",
        )
    )
    configure_account_list_parser(account_commands.add_parser("list"))
    configure_account_get_parser(account_commands.add_parser("get"))
    configure_account_update_parser(account_commands.add_parser("update"))
    configure_account_refresh_parser(account_commands.add_parser("refresh"))
    configure_account_remove_parser(account_commands.add_parser("remove"))

    holdings_parser = top_level.add_parser("holdings", help="Account holdings snapshots.")
    holdings_commands = holdings_parser.add_subparsers(dest="holdings_command")
    configure_holdings_list_parser(holdings_commands.add_parser("list"))
    configure_holdings_capture_parser(holdings_commands.add_parser("capture"))

    source_parser = top_level.add_parser(
        "universe-source",
        help="Durable user-maintained universe extraction sources.",
    )
    source_commands = source_parser.add_subparsers(dest="universe_source_command")
    configure_source_list_parser(source_commands.add_parser("list"))
    configure_source_get_parser(source_commands.add_parser("get"))
    configure_source_create_parser(source_commands.add_parser("create"))
    configure_source_update_parser(source_commands.add_parser("update"))
    configure_source_delete_parser(source_commands.add_parser("delete"))
    configure_source_preview_parser(source_commands.add_parser("preview"))
    configure_source_seed_parser(source_commands.add_parser("seed-defaults"))

    universe_parser = top_level.add_parser(
        "universe",
        help="Universe capability commands.",
    )
    universe_commands = universe_parser.add_subparsers(dest="universe_command")
    configure_universe_list_parser(universe_commands.add_parser("list"))
    configure_universe_get_parser(universe_commands.add_parser("get"))
    configure_universe_delete_parser(universe_commands.add_parser("delete"))
    configure_sync_parser(
        universe_commands.add_parser(
            "sync",
            help="Plan or synchronize a holdings-backed AssetCategory universe.",
        )
    )
    return parser


def _looks_like_asset_price_update_command(argv: Sequence[str]) -> bool:
    return len(argv) >= 2 and argv[0] == "asset" and argv[1] not in {"register", "-h", "--help"}


def _run_safely(operation) -> int:
    """Keep provider/storage exception internals and possible credential context off stderr."""
    try:
        return operation()
    except (LookupError, ValueError) as exc:
        payload = {
            "ok": False,
            "error": {"code": "action_blocked", "message": str(exc), "retryable": False},
        }
    except Exception:
        payload = {
            "ok": False,
            "error": {
                "code": "dependency_unavailable",
                "message": "The operation could not be completed by a required service.",
                "retryable": True,
            },
        }
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    if argv_list and _looks_like_asset_price_update_command(argv_list):
        return _run_safely(lambda: run_asset_price_update_command(argv_list[1:]))

    parser = build_parser()
    args = parser.parse_args(argv_list)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    return _run_safely(lambda: handler(args))
