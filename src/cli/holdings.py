"""CLI for immutable account-holdings snapshots."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict


def configure_list_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account-uid", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--asset-identifiers")
    parser.add_argument("--ordering", default="-time_index")
    parser.set_defaults(handler=run_list_command)


def configure_capture_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account-uid", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--no-register-missing-assets",
        dest="register_missing_assets",
        action="store_false",
        default=True,
    )
    parser.set_defaults(handler=run_capture_command)


def run_list_command(args: argparse.Namespace) -> int:
    from src.holdings import list_account_holdings

    as_of = dt.datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else None
    rows, total = list_account_holdings(
        args.account_uid,
        as_of=as_of,
        limit=args.limit,
        offset=args.offset,
        asset_identifiers=(
            [value.strip() for value in args.asset_identifiers.split(",") if value.strip()]
            if args.asset_identifiers
            else None
        ),
        ordering=args.ordering,
    )
    print(json.dumps({"items": rows, "total": total}, indent=2, default=str))
    return 0


def run_capture_command(args: argparse.Namespace) -> int:
    from src.holdings import capture_alpaca_account_holdings, plan_alpaca_account_holdings

    if not args.execute:
        print(json.dumps(plan_alpaca_account_holdings(args.account_uid), indent=2))
        return 0
    result = capture_alpaca_account_holdings(
        args.account_uid,
        register_missing_assets=args.register_missing_assets,
    )
    print(json.dumps(asdict(result), indent=2, default=str))
    return 0


__all__ = ["configure_capture_parser", "configure_list_parser"]
