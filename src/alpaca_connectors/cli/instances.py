"""CLI for this application's instances of the Markets time series (ms-markets ADR 0047)."""

from __future__ import annotations

import argparse
import json


def configure_backfill_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Copy this application's rows from the shared ms_markets__* time series into its "
        "alpaca_connectors__* instances (signal weights, portfolio values, weights, rebalance "
        "state, calendar events, interpolated prices, orders and trades). Runs as a person who "
        "can read the Markets tables; dry run by default; idempotent. See "
        "docs/operations/markets-api-boundary.md."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--batch-size", type=int, default=2_000)
    parser.add_argument(
        "--register-catalog",
        action="store_true",
        help=(
            "After copying, register the portfolios and signals in the Markets dataset catalog "
            "(needs the Markets workload's data_viewer on the instance tables)."
        ),
    )
    parser.set_defaults(handler=run_backfill_command)


def run_backfill_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.operations.instance_backfill import backfill_instances

    result = backfill_instances(
        execute=args.execute,
        batch_size=args.batch_size,
        register_catalog=args.register_catalog,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


__all__ = ["configure_backfill_parser"]
