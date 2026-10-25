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
        "Register an Alpaca account by importing its keys once from two existing Main Sequence "
        "Secrets; the keys are stored encrypted on the account row and the Secret names are not "
        "kept. Credential values are never accepted by this command; the static site and the "
        "API's HTTP routes are the surfaces that accept them."
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
    parser.add_argument(
        "--api-key-secret-name",
        help="With --secret-key-secret-name: import new keys once from these Secrets.",
    )
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


def configure_migrate_credentials_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Copy the keys of registrations made before revision 0018 from their Main Sequence "
        "Secrets into the encrypted account columns and delete the Secrets this application "
        "created (ADR 0017). Dry run by default; the output never carries a key value."
    )
    parser.add_argument("--execute", action="store_true")
    parser.set_defaults(handler=run_account_migrate_credentials_command)


def run_account_register_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.account.credentials import AlpacaSecretNames
    from alpaca_connectors.account.services import plan_alpaca_account, register_alpaca_account

    common = {
        "credentials": AlpacaSecretNames(
            api_key_secret_name=args.api_key_secret_name,
            secret_key_secret_name=args.secret_key_secret_name,
        ),
        "paper": args.paper,
    }
    if args.plan_only:
        _print(plan_alpaca_account(**common))
        return 0
    result = register_alpaca_account(
        **common,
        account_name=args.account_name,
    )
    _print(result)
    return 0


def run_account_list_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.account.services import list_account_registrations

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
    from alpaca_connectors.account.services import get_account_registration

    item = get_account_registration(args.account_uid)
    if item is None:
        raise SystemExit(f"Alpaca account registration {args.account_uid} does not exist.")
    _print(item)
    return 0


def run_account_update_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.account.services import update_account_registration

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
    if (args.api_key_secret_name is None) != (args.secret_key_secret_name is None):
        raise SystemExit(
            "Importing keys needs both --api-key-secret-name and --secret-key-secret-name."
        )
    credential_secret_names = None
    if args.api_key_secret_name is not None:
        from alpaca_connectors.account.credentials import AlpacaSecretNames

        credential_secret_names = AlpacaSecretNames(
            api_key_secret_name=args.api_key_secret_name,
            secret_key_secret_name=args.secret_key_secret_name,
        )
    _print(
        update_account_registration(
            args.account_uid,
            account_name=args.account_name,
            credential_secret_names=credential_secret_names,
            account_is_active=args.active,
        )
    )
    return 0


def run_account_refresh_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.account.services import refresh_alpaca_account

    _print(refresh_alpaca_account(args.account_uid))
    return 0


def run_account_remove_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.account.services import (
        get_account_registration,
        remove_account_registration,
    )
    from alpaca_connectors.holdings.services import list_account_holdings

    account = get_account_registration(args.account_uid)
    if account is None:
        raise SystemExit(f"Alpaca account registration {args.account_uid} does not exist.")
    _, holdings_count = list_account_holdings(args.account_uid, limit=1)
    preflight = {
        "allowed": True,
        "account_uid": args.account_uid,
        "retained_holdings_rows": holdings_count,
        "credential_source": account.get("credential_source"),
        "detail": (
            "The Alpaca binding, its stored keys included, will be removed and Account will be "
            "deactivated. Main Sequence Secrets a registration made before revision 0018 still "
            "references are deleted when this application created them and kept otherwise."
        ),
    }
    if not args.execute:
        _print(preflight)
        return 0
    _print(remove_account_registration(args.account_uid))
    return 0


def run_account_migrate_credentials_command(args: argparse.Namespace) -> int:
    from alpaca_connectors.account.credential_migration import migrate_account_credentials

    _print(migrate_account_credentials(execute=args.execute))
    return 0


__all__ = [
    "configure_get_parser",
    "configure_list_parser",
    "configure_migrate_credentials_parser",
    "configure_refresh_parser",
    "configure_register_parser",
    "configure_remove_parser",
    "configure_update_parser",
]
