"""CLI for durable ETF portfolio and rebalance configurations."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict
from typing import Any


def _print(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _parse_datetime(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _add_job_arguments(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument("--schedule-type", choices=("interval", "crontab"), required=required)
    parser.add_argument("--schedule-every", type=int)
    parser.add_argument(
        "--schedule-period",
        choices=("seconds", "minutes", "hours", "days"),
    )
    parser.add_argument("--schedule-expression")
    parser.add_argument(
        "--schedule-timezone",
        help="IANA timezone for crontab schedules, for example America/New_York or UTC.",
    )
    parser.add_argument("--schedule-start-time")
    parser.add_argument("--cpu-request", default="0.25" if required else None)
    parser.add_argument("--memory-request", default="0.5" if required else None)
    parser.add_argument("--max-runtime-seconds", type=int, default=3600 if required else None)
    parser.add_argument(
        "--spot",
        action=argparse.BooleanOptionalAction,
        default=False if required else None,
    )


def configure_list_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--search")
    parser.add_argument("--signal-configuration-uid")
    parser.add_argument("--bars-configuration-uid")
    parser.add_argument("--ordering", default="name")
    parser.set_defaults(handler=run_list_command)


def configure_get_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.set_defaults(handler=run_get_command)


def configure_create_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True)
    parser.add_argument("--description")
    parser.add_argument("--signal-configuration-uid", required=True)
    parser.add_argument("--bars-configuration-uid", required=True)
    parser.add_argument("--rebalance-configuration-uid", required=True)
    parser.add_argument("--valuation-column", default="close")
    parser.add_argument("--commission-fee", type=float, default=0.00018)
    parser.add_argument(
        "--forward-fill-to-now", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument(
        "--fail-on-missing-prices", action=argparse.BooleanOptionalAction, default=True
    )
    _add_job_arguments(parser, required=True)
    parser.set_defaults(handler=run_create_command)


def configure_update_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--name")
    parser.add_argument("--description")
    parser.add_argument("--signal-configuration-uid")
    parser.add_argument("--bars-configuration-uid")
    parser.add_argument("--rebalance-configuration-uid")
    parser.add_argument("--valuation-column")
    parser.add_argument("--commission-fee", type=float)
    parser.add_argument(
        "--forward-fill-to-now", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument(
        "--fail-on-missing-prices", action=argparse.BooleanOptionalAction, default=None
    )
    _add_job_arguments(parser, required=False)
    parser.set_defaults(handler=run_update_command)


def configure_delete_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_delete_command)


def configure_action_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.set_defaults(handler=run_run_command)


def configure_runs_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--limit", type=int, default=25)
    parser.set_defaults(handler=run_runs_command)


def configure_prepare_interpolated_prices_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--revision-message")
    parser.set_defaults(handler=run_prepare_interpolated_prices_command)


def configure_rebalance_list_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--search")
    parser.add_argument("--ordering", default="name")
    parser.set_defaults(handler=run_rebalance_list_command)


def configure_rebalance_get_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.set_defaults(handler=run_rebalance_get_command)


def configure_rebalance_create_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True)
    parser.add_argument("--description")
    parser.set_defaults(handler=run_rebalance_create_command)


def configure_rebalance_update_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--name")
    parser.add_argument("--description")
    parser.set_defaults(handler=run_rebalance_update_command)


def configure_rebalance_delete_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_rebalance_delete_command)


def _job_values(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schedule_type": args.schedule_type,
        "schedule_every": args.schedule_every,
        "schedule_period": args.schedule_period,
        "schedule_expression": args.schedule_expression,
        "schedule_timezone": args.schedule_timezone,
        "schedule_start_time": _parse_datetime(args.schedule_start_time),
        "cpu_request": args.cpu_request,
        "memory_request": args.memory_request,
        "max_runtime_seconds": args.max_runtime_seconds,
        "spot": args.spot,
    }


def run_list_command(args: argparse.Namespace) -> int:
    from src.portfolios import list_portfolio_configurations

    rows, total = list_portfolio_configurations(
        limit=args.limit,
        offset=args.offset,
        search=args.search,
        signal_configuration_uid=args.signal_configuration_uid,
        bars_configuration_uid=args.bars_configuration_uid,
        ordering=args.ordering,
    )
    _print({"items": [row.model_dump(mode="json") for row in rows], "total": total})
    return 0


def run_get_command(args: argparse.Namespace) -> int:
    from src.portfolios import get_portfolio_configuration

    row = get_portfolio_configuration(args.configuration_uid)
    if row is None:
        raise SystemExit(f"Portfolio configuration {args.configuration_uid} does not exist.")
    _print(row)
    return 0


def run_create_command(args: argparse.Namespace) -> int:
    from src.operations import create_portfolio_job_configuration

    row = create_portfolio_job_configuration(
        name=args.name,
        description=args.description,
        signal_configuration_uid=args.signal_configuration_uid,
        bars_configuration_uid=args.bars_configuration_uid,
        rebalance_configuration_uid=args.rebalance_configuration_uid,
        valuation_column=args.valuation_column,
        commission_fee=args.commission_fee,
        forward_fill_to_now=args.forward_fill_to_now,
        fail_on_missing_prices=args.fail_on_missing_prices,
        **_job_values(args),
    )
    _print(row)
    return 0


def run_update_command(args: argparse.Namespace) -> int:
    from src.operations import normalize_portfolio_job_settings, update_portfolio_job_configuration

    values = {
        key: value
        for key, value in {
            "name": args.name,
            "description": args.description,
            "signal_configuration_uid": args.signal_configuration_uid,
            "bars_configuration_uid": args.bars_configuration_uid,
            "rebalance_configuration_uid": args.rebalance_configuration_uid,
            "valuation_column": args.valuation_column,
            "commission_fee": args.commission_fee,
            "forward_fill_to_now": args.forward_fill_to_now,
            "fail_on_missing_prices": args.fail_on_missing_prices,
        }.items()
        if value is not None
    }
    settings = None
    if args.schedule_type is not None:
        job_values = _job_values(args)
        job_values["cpu_request"] = job_values["cpu_request"] or "0.25"
        job_values["memory_request"] = job_values["memory_request"] or "0.5"
        job_values["max_runtime_seconds"] = job_values["max_runtime_seconds"] or 3600
        job_values["spot"] = bool(job_values["spot"])
        settings = normalize_portfolio_job_settings(**job_values)
    elif any(value is not None for value in _job_values(args).values()):
        raise SystemExit("Changing Job settings requires a complete --schedule-type definition.")
    if not values and settings is None:
        raise SystemExit("At least one portfolio or Job field must be provided.")
    _print(
        update_portfolio_job_configuration(
            args.configuration_uid,
            job_settings=settings,
            **values,
        )
    )
    return 0


def run_delete_command(args: argparse.Namespace) -> int:
    from src.operations import delete_portfolio_job_configuration
    from src.portfolios import get_portfolio_configuration

    row = get_portfolio_configuration(args.configuration_uid)
    if row is None:
        raise SystemExit(f"Portfolio configuration {args.configuration_uid} does not exist.")
    if not args.execute:
        _print(
            {
                "allowed": True,
                "configuration_uid": str(row.uid),
                "job_uid": str(row.job_uid) if row.job_uid else None,
                "detail": "The dedicated Job and configuration will be deleted; portfolio data is retained.",
            }
        )
        return 0
    _print(delete_portfolio_job_configuration(args.configuration_uid))
    return 0


def run_run_command(args: argparse.Namespace) -> int:
    from src.operations import launch_portfolio_job

    _print(launch_portfolio_job(args.configuration_uid))
    return 0


def run_runs_command(args: argparse.Namespace) -> int:
    from src.operations import list_portfolio_job_runs

    runs = list_portfolio_job_runs(args.configuration_uid, limit=args.limit)
    _print({"items": [asdict(run) for run in runs], "total": len(runs)})
    return 0


def run_prepare_interpolated_prices_command(args: argparse.Namespace) -> int:
    from src.portfolios.interpolated_prices_schema import (
        prepare_interpolated_prices_schema,
    )

    _print(
        prepare_interpolated_prices_schema(
            check_only=args.check_only,
            revision_message=args.revision_message,
        )
    )
    return 0


def run_rebalance_list_command(args: argparse.Namespace) -> int:
    from src.portfolios import list_rebalance_configurations

    rows, total = list_rebalance_configurations(
        limit=args.limit,
        offset=args.offset,
        search=args.search,
        ordering=args.ordering,
    )
    _print({"items": [row.model_dump(mode="json") for row in rows], "total": total})
    return 0


def run_rebalance_get_command(args: argparse.Namespace) -> int:
    from src.portfolios import get_rebalance_configuration

    row = get_rebalance_configuration(args.configuration_uid)
    if row is None:
        raise SystemExit(f"Rebalance configuration {args.configuration_uid} does not exist.")
    _print(row)
    return 0


def run_rebalance_create_command(args: argparse.Namespace) -> int:
    from src.portfolios import create_rebalance_configuration

    _print(create_rebalance_configuration(name=args.name, description=args.description))
    return 0


def run_rebalance_update_command(args: argparse.Namespace) -> int:
    from src.portfolios import update_rebalance_configuration

    values = {
        key: value
        for key, value in {"name": args.name, "description": args.description}.items()
        if value is not None
    }
    if not values:
        raise SystemExit("At least one rebalance configuration field must be provided.")
    _print(update_rebalance_configuration(args.configuration_uid, **values))
    return 0


def run_rebalance_delete_command(args: argparse.Namespace) -> int:
    from src.portfolios import delete_rebalance_configuration, get_rebalance_configuration

    row = get_rebalance_configuration(args.configuration_uid)
    if row is None:
        raise SystemExit(f"Rebalance configuration {args.configuration_uid} does not exist.")
    if not args.execute:
        _print(
            {
                "allowed": True,
                "configuration_uid": str(row.uid),
                "detail": "The rebalance configuration will be deleted if no portfolio references it.",
            }
        )
        return 0
    _print(delete_rebalance_configuration(args.configuration_uid))
    return 0


__all__ = [
    "configure_action_parser",
    "configure_create_parser",
    "configure_delete_parser",
    "configure_get_parser",
    "configure_list_parser",
    "configure_prepare_interpolated_prices_parser",
    "configure_rebalance_create_parser",
    "configure_rebalance_delete_parser",
    "configure_rebalance_get_parser",
    "configure_rebalance_list_parser",
    "configure_rebalance_update_parser",
    "configure_runs_parser",
    "configure_update_parser",
]
