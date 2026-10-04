"""CLI for durable ETF portfolio and rebalance configurations.

Every portfolio configuration runs on the portfolio Job declared in the repository workflow
(ADR 0013). A configuration holds no schedule or compute: `--no-scheduled` leaves it out of that
Job's scheduled run.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any


def _print(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _add_scheduled_argument(parser: argparse.ArgumentParser, *, create: bool) -> None:
    # Schedule and compute belong to the portfolio Job in the workflow file (ADR 0013).
    parser.add_argument(
        "--scheduled",
        action=argparse.BooleanOptionalAction,
        default=True if create else None,
        help="Include the configuration in the portfolio Job's scheduled run.",
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
        "--valuation-maximum-staleness-seconds",
        type=int,
        default=86_400,
        help=(
            "Maximum age of a price selected at a real valuation timestamp; this never "
            "creates or extends timestamps."
        ),
    )
    parser.add_argument(
        "--fail-on-missing-prices", action=argparse.BooleanOptionalAction, default=True
    )
    _add_scheduled_argument(parser, create=True)
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
    parser.add_argument("--valuation-maximum-staleness-seconds", type=int)
    parser.add_argument(
        "--fail-on-missing-prices", action=argparse.BooleanOptionalAction, default=None
    )
    _add_scheduled_argument(parser, create=False)
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
    parser.add_argument(
        "--strategy",
        choices=("calendar_event_signal",),
        default="calendar_event_signal",
    )
    parser.add_argument("--calendar-identifier", default="NYSE")
    parser.add_argument("--session-label", default="regular")
    parser.add_argument(
        "--rebalance-event",
        choices=("market_open", "market_close"),
        default="market_close",
    )
    parser.add_argument("--event-offset-seconds", type=int, default=0)
    parser.add_argument(
        "--rebalance-cadence",
        choices=("every_session", "weekly"),
        default="every_session",
    )
    parser.add_argument("--rebalance-weekday", type=int, choices=range(7), default=0)
    parser.set_defaults(handler=run_rebalance_create_command)


def configure_rebalance_update_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--name")
    parser.add_argument("--description")
    parser.add_argument("--strategy", choices=("calendar_event_signal",))
    parser.add_argument("--calendar-identifier")
    parser.add_argument("--session-label")
    parser.add_argument("--rebalance-event", choices=("market_open", "market_close"))
    parser.add_argument("--event-offset-seconds", type=int)
    parser.add_argument("--rebalance-cadence", choices=("every_session", "weekly"))
    parser.add_argument("--rebalance-weekday", type=int, choices=range(7))
    parser.set_defaults(handler=run_rebalance_update_command)


def configure_rebalance_delete_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("configuration_uid")
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_rebalance_delete_command)


def run_list_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.portfolios import list_portfolio_configurations

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
    from alpaca_connectors.portfolios import get_portfolio_configuration

    row = get_portfolio_configuration(args.configuration_uid)
    if row is None:
        raise SystemExit(f"Portfolio configuration {args.configuration_uid} does not exist.")
    _print(row)
    return 0


def run_create_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.portfolios import create_portfolio_configuration_row

    row = create_portfolio_configuration_row(
        name=args.name,
        description=args.description,
        signal_configuration_uid=args.signal_configuration_uid,
        bars_configuration_uid=args.bars_configuration_uid,
        rebalance_configuration_uid=args.rebalance_configuration_uid,
        valuation_column=args.valuation_column,
        commission_fee=args.commission_fee,
        valuation_maximum_staleness_seconds=args.valuation_maximum_staleness_seconds,
        fail_on_missing_prices=args.fail_on_missing_prices,
        scheduled=args.scheduled,
    )
    _print(row)
    return 0


def run_update_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.portfolios import update_portfolio_configuration

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
            "valuation_maximum_staleness_seconds": (
                args.valuation_maximum_staleness_seconds
            ),
            "fail_on_missing_prices": args.fail_on_missing_prices,
            "scheduled": args.scheduled,
        }.items()
        if value is not None
    }
    if not values:
        raise SystemExit("At least one portfolio configuration field must be provided.")
    _print(update_portfolio_configuration(args.configuration_uid, **values))
    return 0


def run_delete_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.portfolios import (
        delete_portfolio_configuration_row,
        get_portfolio_configuration,
    )

    row = get_portfolio_configuration(args.configuration_uid)
    if row is None:
        raise SystemExit(f"Portfolio configuration {args.configuration_uid} does not exist.")
    if not args.execute:
        _print(
            {
                "allowed": True,
                "configuration_uid": str(row.uid),
                "detail": (
                    "Only the configuration will be deleted; portfolio data and the shared "
                    "Job's runs are retained."
                ),
            }
        )
        return 0
    _print(
        {
            "configuration_uid": str(row.uid),
            "deleted": True,
            "storage_result": delete_portfolio_configuration_row(row.uid),
        }
    )
    return 0


def run_run_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.operations import launch_portfolio_job

    _print(launch_portfolio_job(args.configuration_uid))
    return 0


def run_runs_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.operations import list_portfolio_job_runs

    runs = list_portfolio_job_runs(args.configuration_uid, limit=args.limit)
    _print({"items": [asdict(run) for run in runs], "total": len(runs)})
    return 0


def run_prepare_interpolated_prices_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.portfolios.interpolated_prices_schema import (
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
    from alpaca_connectors.portfolios import list_rebalance_configurations

    rows, total = list_rebalance_configurations(
        limit=args.limit,
        offset=args.offset,
        search=args.search,
        ordering=args.ordering,
    )
    _print({"items": [row.model_dump(mode="json") for row in rows], "total": total})
    return 0


def run_rebalance_get_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.portfolios import get_rebalance_configuration

    row = get_rebalance_configuration(args.configuration_uid)
    if row is None:
        raise SystemExit(f"Rebalance configuration {args.configuration_uid} does not exist.")
    _print(row)
    return 0


def run_rebalance_create_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.portfolios import create_rebalance_configuration

    _print(
        create_rebalance_configuration(
            name=args.name,
            description=args.description,
            strategy=args.strategy,
            calendar_identifier=args.calendar_identifier,
            session_label=args.session_label,
            rebalance_event=args.rebalance_event,
            event_offset_seconds=args.event_offset_seconds,
            rebalance_cadence=args.rebalance_cadence,
            rebalance_weekday=args.rebalance_weekday,
        )
    )
    return 0


def run_rebalance_update_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.portfolios import update_rebalance_configuration

    values = {
        key: value
        for key, value in {
            "name": args.name,
            "description": args.description,
            "strategy": args.strategy,
            "calendar_identifier": args.calendar_identifier,
            "session_label": args.session_label,
            "rebalance_event": args.rebalance_event,
            "event_offset_seconds": args.event_offset_seconds,
            "rebalance_cadence": args.rebalance_cadence,
            "rebalance_weekday": args.rebalance_weekday,
        }.items()
        if value is not None
    }
    if not values:
        raise SystemExit("At least one rebalance configuration field must be provided.")
    _print(update_rebalance_configuration(args.configuration_uid, **values))
    return 0


def run_rebalance_delete_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.portfolios import (
        delete_rebalance_configuration,
        get_rebalance_configuration,
    )

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
