from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .account import (
    configure_backfill_listing_fields_parser as configure_account_backfill_listing_fields_parser,
)
from .account import (
    configure_delete_parser as configure_account_delete_parser,
)
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
    configure_remove_keys_parser as configure_account_remove_keys_parser,
)
from .account import (
    configure_update_parser as configure_account_update_parser,
)
from .asset import configure_link_calendars_parser, configure_register_parser
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
from .instances import configure_backfill_parser as configure_instances_backfill_parser
from .instances import configure_rollback_parser as configure_instances_rollback_parser
from .portfolios import (
    configure_action_parser as configure_portfolio_action_parser,
)
from .portfolios import (
    configure_adopt_parser as configure_portfolio_adopt_parser,
)
from .portfolios import (
    configure_delete_parser as configure_portfolio_delete_parser,
)
from .portfolios import (
    configure_get_parser as configure_portfolio_get_parser,
)
from .portfolios import (
    configure_list_parser as configure_portfolio_list_parser,
)
from .portfolios import (
    configure_prepare_interpolated_prices_parser,
    configure_rebalance_create_parser,
    configure_rebalance_delete_parser,
    configure_rebalance_get_parser,
    configure_rebalance_list_parser,
    configure_rebalance_update_parser,
)
from .portfolios import (
    configure_runs_parser as configure_portfolio_runs_parser,
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
    configure_link_calendars_parser(
        asset_commands.add_parser(
            "link-calendars",
            help="Once per Environment: link each Alpaca asset to its trading calendar.",
        )
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
    configure_account_remove_keys_parser(
        account_commands.add_parser("remove-keys", help="Clear an account's stored keys.")
    )
    configure_account_delete_parser(
        account_commands.add_parser("delete", help="Delete an account and everything it owns.")
    )
    configure_account_backfill_listing_fields_parser(
        account_commands.add_parser(
            "backfill-listing-fields",
            help="One-time: copy account names and active flags onto the account details rows.",
        )
    )

    instances_parser = top_level.add_parser(
        "instances",
        help="This application's instances of the Markets time series (ms-markets ADR 0047).",
    )
    instances_commands = instances_parser.add_subparsers(dest="instances_command")
    configure_instances_backfill_parser(
        instances_commands.add_parser(
            "backfill",
            help="One-time: move this application's history from the shared Markets time series.",
        )
    )
    configure_instances_rollback_parser(
        instances_commands.add_parser(
            "rollback",
            help="Undo the backfill: copy the instances' rows back and unregister them.",
        )
    )

    holdings_parser = top_level.add_parser("holdings", help="Account holdings snapshots.")
    holdings_commands = holdings_parser.add_subparsers(dest="holdings_command")
    configure_holdings_list_parser(holdings_commands.add_parser("list"))
    configure_holdings_capture_parser(holdings_commands.add_parser("capture"))

    portfolio_parser = top_level.add_parser(
        "portfolio",
        help="Review, run and end analytical portfolio configurations on the portfolio Job.",
    )
    portfolio_commands = portfolio_parser.add_subparsers(dest="portfolio_command")
    configure_portfolio_list_parser(portfolio_commands.add_parser("list"))
    configure_portfolio_get_parser(portfolio_commands.add_parser("get"))
    configure_portfolio_delete_parser(portfolio_commands.add_parser("delete"))
    configure_portfolio_adopt_parser(
        portfolio_commands.add_parser(
            "adopt", help="Rollout: give every portfolio its account, or end it (ADR 0028)."
        )
    )
    configure_portfolio_action_parser(portfolio_commands.add_parser("run"))
    configure_portfolio_runs_parser(portfolio_commands.add_parser("runs"))
    configure_prepare_interpolated_prices_parser(
        portfolio_commands.add_parser(
            "prepare-interpolated-prices",
            help="Generate, apply, and verify persistent InterpolatedPrices storage.",
        )
    )
    rebalance_parser = portfolio_commands.add_parser(
        "rebalance",
        help="Manage reusable portfolio rebalance configurations.",
    )
    rebalance_commands = rebalance_parser.add_subparsers(dest="rebalance_command")
    configure_rebalance_list_parser(rebalance_commands.add_parser("list"))
    configure_rebalance_get_parser(rebalance_commands.add_parser("get"))
    configure_rebalance_create_parser(rebalance_commands.add_parser("create"))
    configure_rebalance_update_parser(rebalance_commands.add_parser("update"))
    configure_rebalance_delete_parser(rebalance_commands.add_parser("delete"))
    return parser


def _looks_like_asset_price_update_command(argv: Sequence[str]) -> bool:
    return (
        len(argv) >= 2
        and argv[0] == "asset"
        and argv[1] not in {"register", "link-calendars", "-h", "--help"}
    )


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
