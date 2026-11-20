"""CLI for this application's instances of the Markets time series (ms-markets ADR 0047)."""

from __future__ import annotations

import argparse
import json

_BATCH_HELP = "Keys one copy request reads (ms-markets keys_per_read; its default when omitted)."
_REGISTER_HELP = (
    "Write (backfill) or remove (rollback) the Markets portfolio catalog rows and signal bindings "
    "once every copy is complete; a backfill needs the Markets workload's data_viewer on the "
    "portfolio and signal instance tables."
)


def configure_backfill_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Move this application's history from the shared ms_markets__* time series into its "
        "alpaca_connectors__* instances with the ms-markets series moves (signal weights, "
        "portfolio values, weights and rebalance state, calendar events, interpolated prices, "
        "orders and trades). Runs as a person who can read the Markets tables; dry run by "
        "default; a rerun copies only the gaps. See docs/operations/markets-api-boundary.md."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--register-catalog", action="store_true", help=_REGISTER_HELP)
    parser.add_argument("--batch-size", type=int, help=_BATCH_HELP)
    parser.set_defaults(handler=run_backfill_command)


def configure_rollback_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Roll the move back: copy the rows the instances gained into the shared ms_markets__* "
        "time series and remove the Markets catalog rows and signal bindings. Needs write access "
        "on the shared tables, so it runs only before their roles are revoked, and is followed by "
        "deploying the previous release. Dry run by default."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--keep-catalog",
        action="store_true",
        help="Copy the rows back but leave the catalog rows and signal bindings in place.",
    )
    parser.add_argument("--batch-size", type=int, help=_BATCH_HELP)
    parser.set_defaults(handler=run_rollback_command)


def _print(report: dict) -> int:
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("succeeded") else 1


def run_backfill_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.operations.instance_backfill import backfill_instances

    return _print(
        backfill_instances(
            execute=args.execute,
            register_catalog=args.register_catalog,
            batch_size=args.batch_size,
        )
    )


def run_rollback_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.operations.instance_backfill import rollback_instances

    return _print(
        rollback_instances(
            execute=args.execute,
            register_catalog=not args.keep_catalog,
            batch_size=args.batch_size,
        )
    )


__all__ = ["configure_backfill_parser", "configure_rollback_parser"]
