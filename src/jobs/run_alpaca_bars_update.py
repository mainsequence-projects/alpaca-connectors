"""Execute one stored Alpaca bars configuration as a Main Sequence Job."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Sequence
from typing import Any


def _configuration_uid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("configuration UID must be a valid UUID") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update Alpaca bars using one enabled stored configuration.",
    )
    parser.add_argument(
        "--configuration-uid",
        required=True,
        type=_configuration_uid,
        help="Public UID of the stored Alpaca bars configuration to execute.",
    )
    return parser


def _result_summary(configuration_uid: str, result: dict[str, Any]) -> dict[str, Any]:
    dataset = result.get("dataset") or {}
    return {
        "configuration_uid": configuration_uid,
        "dataset_uid": dataset.get("uid"),
        "asset_source": result.get("asset_source"),
        "asset_count": result.get("asset_count"),
        "rows_persisted": result.get("rows_persisted"),
        "update_hash": result.get("update_hash"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from src.market_data import execute_market_data_update

    result = execute_market_data_update(
        configuration_uid=args.configuration_uid,
        force_update=True,
    )
    print(json.dumps(_result_summary(args.configuration_uid, result), sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
