"""CLI renderers for registered Alpaca accounts."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict


def _print(value) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def configure_register_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Register an Alpaca account using the names of two Main Sequence Secrets. "
        "Credential values are never accepted by this command."
    )
    parser.add_argument("--api-key-secret-name", required=True)
    parser.add_argument("--secret-key-secret-name", required=True)
    parser.add_argument(
        "--paper",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the Alpaca paper environment (default); --no-paper selects live.",
    )
    parser.add_argument("--account-name")
    parser.add_argument("--capture-initial-holdings", action="store_true")
    parser.add_argument(
        "--no-register-missing-assets",
        dest="register_missing_assets",
        action="store_false",
        default=True,
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.set_defaults(handler=run_account_register_command)


def configure_list_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--search")
    parser.add_argument("--active", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--ordering", default="account_name")
    parser.set_defaults(handler=run_account_list_command)


def configure_get_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("account_uid")
    parser.set_defaults(handler=run_account_get_command)


def configure_update_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("account_uid")
    parser.add_argument("--account-name")
    parser.add_argument("--api-key-secret-name")
    parser.add_argument("--secret-key-secret-name")
    parser.add_argument(
        "--active",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.set_defaults(handler=run_account_update_command)


def configure_refresh_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("account_uid")
    parser.set_defaults(handler=run_account_refresh_command)


def configure_remove_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("account_uid")
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_account_remove_command)


def run_account_register_command(args: argparse.Namespace) -> int:
    from src.account.services import plan_alpaca_account, register_alpaca_account

    common = {
        "api_key_secret_name": args.api_key_secret_name,
        "secret_key_secret_name": args.secret_key_secret_name,
        "paper": args.paper,
    }
    if args.plan_only:
        _print(plan_alpaca_account(**common))
        return 0
    result = register_alpaca_account(
        **common,
        account_name=args.account_name,
        capture_initial_holdings=args.capture_initial_holdings,
        register_missing_assets=args.register_missing_assets,
    )
    _print(result)
    return 0


def run_account_list_command(args: argparse.Namespace) -> int:
    from src.account.services import list_account_registrations

    items, total = list_account_registrations(
        limit=args.limit,
        offset=args.offset,
        search=args.search,
        active=args.active,
        ordering=args.ordering,
    )
    _print({"items": items, "total": total, "limit": args.limit, "offset": args.offset})
    return 0


def run_account_get_command(args: argparse.Namespace) -> int:
    from src.account.services import get_account_registration

    item = get_account_registration(args.account_uid)
    if item is None:
        raise SystemExit(f"Alpaca account registration {args.account_uid} does not exist.")
    _print(item)
    return 0


def run_account_update_command(args: argparse.Namespace) -> int:
    from src.account.services import update_account_registration

    if all(
        value is None
        for value in (
            args.account_name,
            args.api_key_secret_name,
            args.secret_key_secret_name,
            args.active,
        )
    ):
        raise SystemExit("At least one account field must be provided.")
    _print(
        update_account_registration(
            args.account_uid,
            account_name=args.account_name,
            api_key_secret_name=args.api_key_secret_name,
            secret_key_secret_name=args.secret_key_secret_name,
            account_is_active=args.active,
        )
    )
    return 0


def run_account_refresh_command(args: argparse.Namespace) -> int:
    from src.account.services import refresh_alpaca_account

    _print(refresh_alpaca_account(args.account_uid))
    return 0


def run_account_remove_command(args: argparse.Namespace) -> int:
    from src.account.services import get_account_registration, remove_account_registration
    from src.holdings.services import list_account_holdings

    account = get_account_registration(args.account_uid)
    if account is None:
        raise SystemExit(f"Alpaca account registration {args.account_uid} does not exist.")
    _, holdings_count = list_account_holdings(args.account_uid, limit=1)
    preflight = {
        "allowed": True,
        "account_uid": args.account_uid,
        "retained_holdings_rows": holdings_count,
        "detail": "The Alpaca binding will be removed and Account will be deactivated.",
    }
    if not args.execute:
        _print(preflight)
        return 0
    _print(remove_account_registration(args.account_uid))
    return 0


__all__ = [
    "configure_get_parser",
    "configure_list_parser",
    "configure_refresh_parser",
    "configure_register_parser",
    "configure_remove_parser",
    "configure_update_parser",
]
