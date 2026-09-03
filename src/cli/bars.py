"""CLI for migrated Alpaca price datasets and account-backed update actions."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict
from typing import Any

from src.market_data import normalize_frequency_id

DEFAULT_PRICE_UPDATE_FEED = "sip"
DEFAULT_PRICE_UPDATE_ADJUSTMENT = "all"
PRICE_UPDATE_ACTION = "update_prices"


def _print(value: Any) -> None:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def parse_csv(raw_value: str) -> list[str]:
    values = [value.strip() for value in raw_value.split(",") if value.strip()]
    if not values:
        raise ValueError("At least one value must be provided.")
    return list(dict.fromkeys(values))


def parse_tickers(raw_value: str) -> list[str]:
    return [value.upper() for value in parse_csv(raw_value)]


def normalize_price_update_period(period: str) -> str:
    normalized_period = period.strip().lower()
    aliases = {"daily": "1d", "day": "1d"}
    return normalize_frequency_id(aliases.get(normalized_period, normalized_period))


def configure_run_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = "Resolve or execute one stored Alpaca bar configuration."
    parser.add_argument("--configuration-uid", required=True)
    parser.add_argument("--hash-namespace")
    parser.add_argument("--force-update", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_bars_command)


def configure_configuration_list_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--search")
    parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--asset-source", choices=("assets", "universe", "account_holdings"))
    parser.add_argument("--ordering", default="name")
    parser.set_defaults(handler=run_configuration_list_command)


def configure_configuration_get_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.set_defaults(handler=run_configuration_get_command)


def _add_configuration_values(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument("--name", required=required)
    parser.add_argument("--description")
    parser.add_argument("--account-uid", required=required)
    parser.add_argument(
        "--asset-source",
        required=required,
        choices=("assets", "universe", "account_holdings"),
    )
    parser.add_argument("--asset-uids", help="Comma-separated Asset UIDs for the assets source.")
    parser.add_argument("--universe-uid", help="AssetCategory UID for the universe source.")
    parser.add_argument("--frequency", required=required)
    parser.add_argument("--feed", required=required)
    parser.add_argument("--adjustment", required=required)
    parser.add_argument(
        "--enabled",
        action=argparse.BooleanOptionalAction,
        default=True if required else None,
    )


def configure_configuration_create_parser(parser: argparse.ArgumentParser) -> None:
    _add_configuration_values(parser, required=True)
    parser.set_defaults(handler=run_configuration_create_command)


def configure_configuration_update_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    _add_configuration_values(parser, required=False)
    parser.set_defaults(handler=run_configuration_update_command)


def configure_configuration_delete_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_configuration_delete_command)


def configure_dataset_list_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ordering", default="frequency_id")
    parser.set_defaults(handler=run_dataset_list_command)


def configure_dataset_get_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("dataset_uid")
    parser.set_defaults(handler=run_dataset_get_command)


def configure_prices_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset-uid", required=True)
    parser.add_argument("--asset-uids")
    parser.add_argument("--asset-identifiers")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--ordering", default="-time_index")
    parser.set_defaults(handler=run_prices_command)


def run_bars_command(args: argparse.Namespace) -> int:
    from src.market_data import build_market_data_update, execute_market_data_update

    values = {
        "configuration_uid": args.configuration_uid,
        "hash_namespace": args.hash_namespace,
    }
    if not args.execute:
        _, summary = build_market_data_update(**values)
        _print({"allowed": True, **summary})
        return 0
    _print(execute_market_data_update(**values, force_update=args.force_update))
    return 0


def run_configuration_list_command(args: argparse.Namespace) -> int:
    from src.market_data import list_bar_configurations

    items, total = list_bar_configurations(
        limit=args.limit,
        offset=args.offset,
        search=args.search,
        enabled=args.enabled,
        asset_source=args.asset_source,
        ordering=args.ordering,
    )
    _print({"items": [item.model_dump(mode="json") for item in items], "total": total})
    return 0


def run_configuration_get_command(args: argparse.Namespace) -> int:
    from src.market_data import get_bar_configuration

    item = get_bar_configuration(args.configuration_uid)
    if item is None:
        raise SystemExit(f"Bar configuration {args.configuration_uid} does not exist.")
    _print(item.model_dump(mode="json"))
    return 0


def _configuration_values(args: argparse.Namespace, *, exclude_none: bool) -> dict[str, Any]:
    values = {
        "name": args.name,
        "description": args.description,
        "account_uid": args.account_uid,
        "asset_source": args.asset_source,
        "asset_uids": parse_csv(args.asset_uids) if args.asset_uids else None,
        "universe_uid": args.universe_uid,
        "frequency_id": args.frequency,
        "feed": args.feed,
        "adjustment": args.adjustment,
        "enabled": args.enabled,
    }
    return (
        {key: value for key, value in values.items() if value is not None}
        if exclude_none
        else values
    )


def run_configuration_create_command(args: argparse.Namespace) -> int:
    from src.market_data import create_bar_configuration

    _print(create_bar_configuration(**_configuration_values(args, exclude_none=False)))
    return 0


def run_configuration_update_command(args: argparse.Namespace) -> int:
    from src.market_data import update_bar_configuration

    values = _configuration_values(args, exclude_none=True)
    if not values:
        raise SystemExit("At least one bar-configuration field must be provided.")
    _print(update_bar_configuration(args.configuration_uid, **values))
    return 0


def run_configuration_delete_command(args: argparse.Namespace) -> int:
    from src.market_data import delete_bar_configuration, get_bar_configuration

    item = get_bar_configuration(args.configuration_uid)
    if item is None:
        raise SystemExit(f"Bar configuration {args.configuration_uid} does not exist.")
    if not args.execute:
        _print(
            {
                "allowed": True,
                "configuration_uid": args.configuration_uid,
                "detail": "The stored configuration and explicit memberships will be deleted.",
            }
        )
        return 0
    _print(delete_bar_configuration(args.configuration_uid))
    return 0


def run_dataset_list_command(args: argparse.Namespace) -> int:
    from src.market_data import list_market_data_datasets

    datasets = list_market_data_datasets()
    descending = args.ordering.startswith("-")
    ordering_key = args.ordering.removeprefix("-")
    if ordering_key not in {"frequency_id", "feed", "adjustment"}:
        raise ValueError(f"Unsupported dataset ordering {args.ordering!r}.")
    datasets.sort(key=lambda item: getattr(item, ordering_key), reverse=descending)
    _print({"items": [asdict(dataset) for dataset in datasets], "total": len(datasets)})
    return 0


def run_dataset_get_command(args: argparse.Namespace) -> int:
    from src.market_data import get_market_data_dataset

    dataset = get_market_data_dataset(args.dataset_uid)
    if dataset is None:
        raise SystemExit(f"Market-data dataset {args.dataset_uid} does not exist.")
    _print(dataset)
    return 0


def _parse_datetime(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def run_prices_command(args: argparse.Namespace) -> int:
    from src.market_data import query_price_observations

    rows, total = query_price_observations(
        args.dataset_uid,
        asset_uids=parse_csv(args.asset_uids) if args.asset_uids else None,
        asset_identifiers=parse_csv(args.asset_identifiers) if args.asset_identifiers else None,
        start=_parse_datetime(args.start),
        end=_parse_datetime(args.end),
        limit=args.limit,
        offset=args.offset,
        ordering=args.ordering,
    )
    _print({"items": rows, "total": total})
    return 0


def build_asset_price_update_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpaca-connectors asset <ticker> update_prices <period>")
    parser.add_argument("ticker")
    parser.add_argument("asset_action", choices=(PRICE_UPDATE_ACTION,))
    parser.add_argument("period")
    parser.add_argument("--configuration-uid", required=True)
    parser.add_argument("--hash-namespace")
    parser.add_argument("--force-update", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute", action="store_true")
    return parser


def run_asset_price_update_command(argv: list[str]) -> int:
    from src.assets.resolution import assets_for_ticker
    from src.market_data import build_market_data_update, execute_market_data_update

    args = build_asset_price_update_parser().parse_args(argv)
    assets = assets_for_ticker(args.ticker.strip().upper())
    if len(assets) != 1:
        raise SystemExit(
            f"Ticker {args.ticker!r} must resolve to exactly one registered Main Sequence Asset."
        )
    values = {
        "configuration_uid": args.configuration_uid,
        "hash_namespace": args.hash_namespace,
    }
    _, summary = build_market_data_update(**values)
    requested_frequency = normalize_price_update_period(args.period)
    if summary["dataset"]["frequency_id"] != requested_frequency:
        raise ValueError(
            f"Stored configuration frequency is {summary['dataset']['frequency_id']!r}, not "
            f"{requested_frequency!r}."
        )
    if assets[0].unique_identifier not in summary["asset_identifiers"]:
        raise ValueError(
            f"Ticker {args.ticker!r} is not present in the stored configuration's resolved scope."
        )
    if not args.execute:
        _print({"allowed": True, "requested_period": args.period, **summary})
        return 0
    _print(execute_market_data_update(**values, force_update=args.force_update))
    return 0


__all__ = [
    "DEFAULT_PRICE_UPDATE_ADJUSTMENT",
    "DEFAULT_PRICE_UPDATE_FEED",
    "configure_dataset_get_parser",
    "configure_dataset_list_parser",
    "configure_configuration_create_parser",
    "configure_configuration_delete_parser",
    "configure_configuration_get_parser",
    "configure_configuration_list_parser",
    "configure_configuration_update_parser",
    "configure_prices_parser",
    "configure_run_parser",
    "normalize_price_update_period",
    "parse_tickers",
    "run_asset_price_update_command",
]
