from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.cli import main as run_cli
from src.holdings_categories import (
    build_holdings_asset_category_unique_identifier,
    infer_holdings_component_provider,
)


@dataclass(frozen=True)
class RoutineSpec:
    etf_ticker: str
    component_provider: str
    frequency_id: str
    feed: str
    adjustment: str
    etf_update_period: str
    include_seed_registration: bool
    include_category_sync: bool
    include_etf_price_update: bool
    include_category_price_update: bool


def _default_routine_path() -> Path:
    return REPO_ROOT / "data" / "etf_maintenance_routines.yaml"


def _coerce_bool(raw: Any, *, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    raise ValueError(f"Expected boolean value, got {raw!r}.")


def _coerce_str(raw: Any, *, default: str | None = None) -> str | None:
    if raw is None:
        return default
    if isinstance(raw, str):
        value = raw.strip()
        return value if value else default
    raise ValueError(f"Expected string value, got {raw!r}.")


def _coerce_routine_ticker(raw: Any) -> str:
    ticker = _coerce_str(raw, default=None)
    if not ticker:
        raise ValueError("Each routine entry must include a non-empty `etf_ticker`.")
    return ticker.upper()


def _load_routines(path: Path) -> list[RoutineSpec]:
    if not path.exists():
        raise FileNotFoundError(f"Routine file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    if not isinstance(payload, dict):
        raise ValueError("Routine file must contain a mapping.")

    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    routines = payload.get("routines")
    if not isinstance(routines, list):
        raise ValueError("Routine file must define `routines` as a list.")

    specs: list[RoutineSpec] = []
    for routine in routines:
        if not isinstance(routine, dict):
            raise ValueError("Each routine entry must be a mapping.")

        etf_ticker = _coerce_routine_ticker(routine.get("etf_ticker"))
        component_provider = _coerce_str(routine.get("component_provider"), default=None)
        if component_provider is None:
            component_provider = _coerce_str(
                defaults.get("component_provider"), default=None
            )
            if component_provider is None:
                component_provider = infer_holdings_component_provider(etf_ticker)

        specs.append(
            RoutineSpec(
                etf_ticker=etf_ticker,
                component_provider=component_provider,
                frequency_id=_coerce_str(
                    routine.get("frequency_id"),
                    default=_coerce_str(defaults.get("frequency_id"), default="1d"),
                )
                or "1d",
                feed=_coerce_str(
                    routine.get("feed"),
                    default=_coerce_str(defaults.get("feed"), default="sip"),
                )
                or "sip",
                adjustment=_coerce_str(
                    routine.get("adjustment"),
                    default=_coerce_str(defaults.get("adjustment"), default="all"),
                )
                or "all",
                etf_update_period=_coerce_str(
                    routine.get("etf_update_period"),
                    default=_coerce_str(
                        defaults.get("etf_update_period"), default="daily"
                    ),
                )
                or "daily",
                include_seed_registration=_coerce_bool(
                    routine.get("include_seed_registration"),
                    default=_coerce_bool(
                        defaults.get("include_seed_registration"), default=True
                    ),
                ),
                include_category_sync=_coerce_bool(
                    routine.get("include_category_sync"),
                    default=_coerce_bool(defaults.get("include_category_sync"), default=True),
                ),
                include_etf_price_update=_coerce_bool(
                    routine.get("include_etf_price_update"),
                    default=_coerce_bool(
                        defaults.get("include_etf_price_update"), default=True
                    ),
                ),
                include_category_price_update=_coerce_bool(
                    routine.get("include_category_price_update"),
                    default=_coerce_bool(
                        defaults.get("include_category_price_update"), default=True
                    ),
                ),
            )
        )
    return specs


def _run_cli(argv: list[str]) -> int:
    try:
        return run_cli(list(argv))
    except SystemExit as exc:
        return 1 if exc.code is None else int(exc.code)


def _run_command(label: str, argv: list[str]) -> tuple[str, int, list[str]]:
    return (label, _run_cli(argv), argv)


def _build_routine_commands(
    routine: RoutineSpec,
    *,
    dry_run: bool,
) -> list[tuple[str, list[str]]]:
    category_identifier = build_holdings_asset_category_unique_identifier(
        routine.etf_ticker
    )

    commands: list[tuple[str, list[str]]] = []
    if routine.include_seed_registration:
        register_argv = [
            "asset",
            "register",
            "--seed-tickers",
            routine.etf_ticker,
            "--component-provider",
            routine.component_provider,
        ]
        if not dry_run:
            register_argv.append("--execute")
        commands.append(("register_holdings_assets", register_argv))

    if routine.include_category_sync:
        category_argv = ["holdings-category", "create", "--etf-ticker", routine.etf_ticker]
        if not dry_run:
            category_argv.append("--execute")
        commands.append(("sync_holdings_category", category_argv))

    if routine.include_etf_price_update:
        etf_price_argv = [
            "asset",
            routine.etf_ticker,
            "update_prices",
            routine.etf_update_period,
        ]
        if dry_run:
            etf_price_argv.append("--plan-only")
        commands.append(("update_etf_prices", etf_price_argv))

    if routine.include_category_price_update:
        category_price_argv = [
            "bars",
            "run",
            "--asset-category-unique-identifier",
            category_identifier,
            "--frequency-id",
            routine.frequency_id,
            "--feed",
            routine.feed,
            "--adjustment",
            routine.adjustment,
        ]
        if dry_run:
            category_price_argv.append("--plan-only")
        commands.append(("update_category_prices", category_price_argv))

    return commands


def run_routines(
    routines: list[RoutineSpec],
    *,
    dry_run: bool,
    continue_on_error: bool,
) -> int:
    failures: list[tuple[str, int, list[str]]] = []
    total_steps = 0

    if dry_run:
        print("[dry-run] planned routine commands:")

    for routine in routines:
        commands = _build_routine_commands(routine, dry_run=dry_run)

        for command_label, argv in commands:
            command_text = " ".join(argv)
            total_steps += 1
            if dry_run:
                print(f"  {routine.etf_ticker}: {command_label} -> {command_text}")
                continue

            status = _run_command(command_label, argv)
            if status[1] != 0:
                failures.append(status)
                if not continue_on_error:
                    print(
                        f"Failed after {total_steps} steps in routine {routine.etf_ticker}: "
                        f"{status[2]}"
                    )
                    return 1

    if failures:
        print("Completed with failures.")
        for status in failures:
            print(f"  - {' '.join(status[2])}: status={status[1]}")
        return 1

    print(
        f"Completed {total_steps} routine steps across {len(routines)} ETFs"
        f"{' (dry run)' if dry_run else ''}."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a list of ETF maintenance routines: holdings registration, category sync, "
            "ETF price update, then category price update."
        )
    )
    parser.add_argument(
        "--routines-path",
        default=str(_default_routine_path()),
        help="Path to the YAML routine list.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build routine commands and run in dry-run mode.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue with remaining routines when an individual command fails.",
    )

    args = parser.parse_args(argv)
    specs = _load_routines(Path(args.routines_path))
    return run_routines(
        specs,
        dry_run=args.dry_run,
        continue_on_error=args.continue_on_error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
