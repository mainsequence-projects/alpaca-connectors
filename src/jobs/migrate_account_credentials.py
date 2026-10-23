"""Entry point of the one-off `alpaca-credential-migration` Job (ADR 0017).

Copies every account registration's Alpaca keys from the Main Sequence Secrets it referenced
before revision 0018 into the encrypted columns of `AlpacaAccountDetails`, and deletes the Secrets
this application created. Dry run by default; `--execute` writes. The schema gate (ADR 0012) comes
first. The summary carries counts, Secret names and reasons, never a key value.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy registered Alpaca account keys into the encrypted account columns.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the keys and delete the managed Secrets; without it, report only.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from alpaca_connectors.migrations.schema_gate import skip_if_schema_behind

    if skip_if_schema_behind("Alpaca credential migration Job"):
        return 0

    from alpaca_connectors.account.credential_migration import migrate_account_credentials

    summary = migrate_account_credentials(execute=args.execute)
    print(json.dumps(summary, sort_keys=True, default=str), flush=True)
    return 1 if summary["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
