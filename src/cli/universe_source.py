"""CLI for durable universe extraction sources."""

from __future__ import annotations

import argparse
import json


def _print(value) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def configure_list_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--search")
    parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--ordering", default="name")
    parser.set_defaults(handler=run_list_command)


def configure_get_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source_uid")
    parser.set_defaults(handler=run_get_command)


def configure_create_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.set_defaults(handler=run_create_command)


def configure_update_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source_uid")
    parser.add_argument("--name")
    parser.add_argument("--symbol")
    parser.add_argument("--url")
    parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    parser.set_defaults(handler=run_update_command)


def configure_delete_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source_uid")
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_delete_command)


def configure_preview_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source_uid")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.set_defaults(handler=run_preview_command)


def configure_seed_parser(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(handler=run_seed_command)


def run_list_command(args: argparse.Namespace) -> int:
    from src.universes import list_universe_sources

    items, total = list_universe_sources(
        limit=args.limit,
        offset=args.offset,
        search=args.search,
        enabled=args.enabled,
        ordering=args.ordering,
    )
    _print({"items": [item.model_dump(mode="json") for item in items], "total": total})
    return 0


def run_get_command(args: argparse.Namespace) -> int:
    from src.universes import get_universe_source

    item = get_universe_source(args.source_uid)
    if item is None:
        raise SystemExit(f"Universe source {args.source_uid} does not exist.")
    _print(item)
    return 0


def run_create_command(args: argparse.Namespace) -> int:
    from src.universes import create_universe_source

    _print(
        create_universe_source(
            name=args.name,
            symbol=args.symbol,
            source_url=args.url,
            enabled=args.enabled,
        )
    )
    return 0


def run_update_command(args: argparse.Namespace) -> int:
    from src.universes import update_universe_source

    if all(value is None for value in (args.name, args.symbol, args.url, args.enabled)):
        raise SystemExit("At least one universe-source field must be provided.")
    _print(
        update_universe_source(
            args.source_uid,
            name=args.name,
            symbol=args.symbol,
            source_url=args.url,
            enabled=args.enabled,
        )
    )
    return 0


def run_delete_command(args: argparse.Namespace) -> int:
    from src.universes import delete_universe_source, get_universe_source

    item = get_universe_source(args.source_uid)
    if item is None:
        raise SystemExit(f"Universe source {args.source_uid} does not exist.")
    if not args.execute:
        _print(
            {
                "allowed": True,
                "source_uid": args.source_uid,
                "detail": "Only the source row will be deleted; any AssetCategory is retained.",
            }
        )
        return 0
    _print(delete_universe_source(args.source_uid))
    return 0


def run_preview_command(args: argparse.Namespace) -> int:
    from src.universes import preview_universe_source

    _print(preview_universe_source(args.source_uid, timeout=args.timeout).summary())
    return 0


def run_seed_command(_args: argparse.Namespace) -> int:
    from src.universes import seed_default_universe_sources

    rows = seed_default_universe_sources()
    _print({"items": [row.model_dump(mode="json") for row in rows], "total": len(rows)})
    return 0


__all__ = [
    "configure_create_parser",
    "configure_delete_parser",
    "configure_get_parser",
    "configure_list_parser",
    "configure_preview_parser",
    "configure_seed_parser",
    "configure_update_parser",
]
