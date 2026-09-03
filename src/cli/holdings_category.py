"""CLI for reading and materializing ms-markets AssetCategory universes."""

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
    parser.add_argument("category_uid")
    parser.set_defaults(handler=run_get_command)


def configure_delete_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("category_uid")
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_delete_command)


def configure_sync_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-uid", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.set_defaults(handler=run_sync_command)


def run_sync_command(args: argparse.Namespace) -> int:
    from src.universes import preview_universe_source, sync_universe_source

    plan = preview_universe_source(args.source_uid, timeout=args.timeout)
    _print(plan.summary())
    if not args.execute:
        print("Dry run only. Pass --execute to create or refresh the AssetCategory.")
        return 0
    if plan.has_blockers():
        raise SystemExit(
            "Refusing to synchronize the universe because its assets are not fully resolved."
        )
    result = sync_universe_source(args.source_uid, timeout=args.timeout)
    _print(
        {
            "unique_identifier": result.unique_identifier,
            "display_name": result.display_name,
            "asset_uids": [str(uid) for uid in result.asset_uids],
            "asset_count": len(result.asset_uids),
        }
    )
    return 0


def run_list_command(args: argparse.Namespace) -> int:
    from src.universes import list_materialized_universes

    items, total = list_materialized_universes(
        limit=args.limit,
        offset=args.offset,
        search=args.search,
        ordering=args.ordering,
    )
    _print({"items": items, "total": total, "limit": args.limit, "offset": args.offset})
    return 0


def run_get_command(args: argparse.Namespace) -> int:
    from src.universes import get_materialized_universe

    item = get_materialized_universe(args.category_uid)
    if item is None:
        raise SystemExit(f"Universe {args.category_uid} does not exist.")
    _print(item)
    return 0


def run_delete_command(args: argparse.Namespace) -> int:
    from src.universes import delete_materialized_universe, get_materialized_universe

    item = get_materialized_universe(args.category_uid)
    if item is None:
        raise SystemExit(f"Universe {args.category_uid} does not exist.")
    if not args.execute:
        _print(
            {
                "allowed": True,
                "category_uid": args.category_uid,
                "asset_count": item["asset_count"],
                "detail": "The AssetCategory and its memberships will be deleted.",
            }
        )
        return 0
    _print(delete_materialized_universe(args.category_uid))
    return 0


__all__ = [
    "configure_delete_parser",
    "configure_get_parser",
    "configure_list_parser",
    "configure_sync_parser",
]
