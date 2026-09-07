"""CLI for Universe-backed Alpaca ETF signal Job configurations."""

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


def _configuration_payload(configuration: Any) -> dict[str, Any]:
    from src.operations import signal_uid_for_configuration

    return {
        **configuration.model_dump(mode="json"),
        "signal_uid": signal_uid_for_configuration(configuration),
    }


def configure_list_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--search")
    parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--lifecycle-state",
        choices=("provisioning", "ready", "paused", "error", "deleting"),
    )
    parser.add_argument("--ordering", default="name")
    parser.set_defaults(handler=run_list_command)


def configure_get_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.set_defaults(handler=run_get_command)


def _add_configuration_values(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument("--name", required=required)
    parser.add_argument("--description")
    parser.add_argument("--universe-uid", required=required)
    parser.add_argument("--account-uid", required=required)
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
    parser.add_argument(
        "--enabled",
        action=argparse.BooleanOptionalAction,
        default=True if required else None,
    )


def configure_create_parser(parser: argparse.ArgumentParser) -> None:
    _add_configuration_values(parser, required=True)
    parser.set_defaults(handler=run_create_command)


def configure_update_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    _add_configuration_values(parser, required=False)
    parser.set_defaults(handler=run_update_command)


def configure_delete_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_delete_command)


def configure_action_parser(parser: argparse.ArgumentParser, handler) -> None:
    parser.add_argument("configuration_uid")
    parser.set_defaults(handler=handler)


def configure_runs_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--limit", type=int, default=25)
    parser.set_defaults(handler=run_runs_command)


def _configuration_values(args: argparse.Namespace, *, exclude_none: bool) -> dict[str, Any]:
    values = {
        "name": args.name,
        "description": args.description,
        "universe_uid": args.universe_uid,
        "account_uid": args.account_uid,
        "enabled": args.enabled,
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
    return (
        {key: value for key, value in values.items() if value is not None}
        if exclude_none
        else values
    )


def run_list_command(args: argparse.Namespace) -> int:
    from src.operations import list_signal_job_configurations

    items, total = list_signal_job_configurations(
        limit=args.limit,
        offset=args.offset,
        search=args.search,
        enabled=args.enabled,
        lifecycle_state=args.lifecycle_state,
        ordering=args.ordering,
    )
    _print(
        {
            "items": [_configuration_payload(item) for item in items],
            "total": total,
        }
    )
    return 0


def run_get_command(args: argparse.Namespace) -> int:
    from src.operations import get_signal_job_configuration

    item = get_signal_job_configuration(args.configuration_uid)
    if item is None:
        raise SystemExit(f"Signal Job configuration {args.configuration_uid} does not exist.")
    _print(_configuration_payload(item))
    return 0


def run_create_command(args: argparse.Namespace) -> int:
    from src.operations import create_signal_job_configuration

    item = create_signal_job_configuration(**_configuration_values(args, exclude_none=False))
    _print(_configuration_payload(item))
    return 0


def run_update_command(args: argparse.Namespace) -> int:
    from src.operations import update_signal_job_configuration

    values = _configuration_values(args, exclude_none=True)
    if not values:
        raise SystemExit("At least one signal Job configuration field must be provided.")
    item = update_signal_job_configuration(args.configuration_uid, **values)
    _print(_configuration_payload(item))
    return 0


def run_delete_command(args: argparse.Namespace) -> int:
    from src.operations import delete_signal_job_configuration, get_signal_job_configuration

    item = get_signal_job_configuration(args.configuration_uid)
    if item is None:
        raise SystemExit(f"Signal Job configuration {args.configuration_uid} does not exist.")
    if not args.execute:
        _print(
            {
                "allowed": True,
                "configuration_uid": str(item.uid),
                "job_uid": str(item.job_uid) if item.job_uid else None,
                "detail": "The dedicated Job and its configuration will be deleted. Signal data is retained.",
            }
        )
        return 0
    _print(
        delete_signal_job_configuration(args.configuration_uid)
    )
    return 0


def run_run_command(args: argparse.Namespace) -> int:
    from src.operations import launch_signal_job

    _print(
        launch_signal_job(args.configuration_uid)
    )
    return 0


def run_pause_command(args: argparse.Namespace) -> int:
    from src.operations import pause_signal_job_configuration

    _print(_configuration_payload(pause_signal_job_configuration(args.configuration_uid)))
    return 0


def run_resume_command(args: argparse.Namespace) -> int:
    from src.operations import resume_signal_job_configuration

    _print(_configuration_payload(resume_signal_job_configuration(args.configuration_uid)))
    return 0


def run_reconcile_command(args: argparse.Namespace) -> int:
    from src.operations import reconcile_signal_job_configuration

    _print(_configuration_payload(reconcile_signal_job_configuration(args.configuration_uid)))
    return 0


def run_runs_command(args: argparse.Namespace) -> int:
    from src.operations import list_signal_job_runs

    runs = list_signal_job_runs(args.configuration_uid, limit=args.limit)
    _print({"items": [asdict(run) for run in runs], "total": len(runs)})
    return 0


__all__ = [
    "configure_action_parser",
    "configure_create_parser",
    "configure_delete_parser",
    "configure_get_parser",
    "configure_list_parser",
    "configure_runs_parser",
    "configure_update_parser",
    "run_pause_command",
    "run_reconcile_command",
    "run_resume_command",
    "run_run_command",
]
