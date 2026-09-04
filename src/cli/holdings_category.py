"""CLI for registered Asset Universes and their materialized categories."""

from __future__ import annotations

import argparse
import json


def _print(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def configure_list_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--search")
    parser.add_argument("--ordering", default="display_name")
    parser.set_defaults(handler=run_list_command)


def configure_get_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("universe_uid")
    parser.set_defaults(handler=run_get_command)


def configure_delete_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("universe_uid")
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_delete_command)


def configure_run_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--universe-uid", required=True)
    parser.add_argument("--account-uid", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.set_defaults(handler=run_run_command)


def run_run_command(args: argparse.Namespace) -> int:
    from src.universes import (
        get_asset_universe_view,
        preview_asset_universe,
        run_asset_universe,
    )

    universe = get_asset_universe_view(args.universe_uid)
    if universe is None:
        raise SystemExit(f"Asset Universe {args.universe_uid} does not exist.")
    if not args.execute:
        plan = preview_asset_universe(
            args.universe_uid,
            account_uid=args.account_uid,
            timeout=args.timeout,
        )
        _print(plan.summary())
        print(
            "Dry run only. Pass --execute to extract components, bulk-register missing Alpaca "
            "assets, refresh the linked AssetCategory, and publish one signal observation."
        )
        return 0
    result = run_asset_universe(
        args.universe_uid,
        account_uid=args.account_uid,
        timeout=args.timeout,
    )
    _print(
        {
            "universe_uid": args.universe_uid,
            "source_uid": universe["source_uid"],
            "asset_category_uid": universe["asset_category_uid"],
            "account_uid": result.account_uid,
            "asset_uids": [str(uid) for uid in result.asset_uids],
            "asset_count": len(result.asset_uids),
            "observed_at": result.observed_at,
            "signal_uid": result.signal_uid,
            "signal_weight_row_count": result.signal_weight_row_count,
            "component_weights_by_symbol": result.component_weights_by_symbol,
            "asset_identifiers_by_symbol": result.asset_identifiers_by_symbol,
            "existing_asset_uids_by_symbol": result.existing_asset_uids_by_symbol,
            "created_asset_uids_by_symbol": result.created_asset_uids_by_symbol,
            "openfigi_unmatched_symbols": result.openfigi_unmatched_symbols,
        }
    )
    return 0


def run_list_command(args: argparse.Namespace) -> int:
    from src.universes import list_asset_universes

    items, total = list_asset_universes(
        limit=args.limit,
        offset=args.offset,
        search=args.search,
        ordering=args.ordering,
    )
    _print({"items": items, "total": total, "limit": args.limit, "offset": args.offset})
    return 0


def run_get_command(args: argparse.Namespace) -> int:
    from src.universes import get_asset_universe_view

    item = get_asset_universe_view(args.universe_uid)
    if item is None:
        raise SystemExit(f"Asset Universe {args.universe_uid} does not exist.")
    _print(item)
    return 0


def run_delete_command(args: argparse.Namespace) -> int:
    from src.market_data.configurations import bar_configurations_for_universe
    from src.universes import delete_asset_universe, get_asset_universe_view

    item = get_asset_universe_view(args.universe_uid)
    if item is None:
        raise SystemExit(f"Asset Universe {args.universe_uid} does not exist.")
    dependencies = bar_configurations_for_universe(args.universe_uid)
    if not args.execute:
        _print(
            {
                "allowed": not dependencies,
                "universe_uid": args.universe_uid,
                "source_uid": item["source_uid"],
                "asset_category_uid": item["asset_category_uid"],
                "asset_count": item["asset_count"],
                "blocking_bar_configurations": [
                    {"uid": str(configuration.uid), "name": configuration.name}
                    for configuration in dependencies
                ],
                "detail": (
                    "The Asset Universe, its linked category, and memberships will be deleted."
                    if not dependencies
                    else "Delete or change the blocking bar configurations first."
                ),
            }
        )
        return 0
    _print(delete_asset_universe(args.universe_uid))
    return 0


__all__ = [
    "configure_delete_parser",
    "configure_get_parser",
    "configure_list_parser",
    "configure_run_parser",
]
