from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.assets.alpaca_us_equities import (
    build_alpaca_us_equity_registration_plan,
    register_alpaca_us_equity_assets,
    resolve_alpaca_us_equity_registration_plan,
)
from src.cli.bars import (
    DEFAULT_PRICE_UPDATE_ADJUSTMENT,
    DEFAULT_PRICE_UPDATE_FEED,
    _run_stock_bars_node,
    build_stock_bars_node,
    normalize_price_update_period,
)
from src.etf_holdings import (
    EtfExpansionRequest,
    build_holdings_asset_category_plan,
    build_holdings_asset_category_unique_identifier,
    expand_etf_seed_symbols,
    infer_holdings_component_provider,
    sync_holdings_asset_category,
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


@dataclass(frozen=True)
class RoutineStep:
    label: str
    preview_text: str
    runner: Callable[[], None]


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


def _execute_seed_registration(routine: RoutineSpec) -> None:
    expansion_result = expand_etf_seed_symbols(
        EtfExpansionRequest(
            seed_tickers=[routine.etf_ticker],
            component_provider=routine.component_provider,
        )
    )
    plan = build_alpaca_us_equity_registration_plan(
        symbols=expansion_result.symbols_for_registration,
    )
    resolution = resolve_alpaca_us_equity_registration_plan(plan)
    register_alpaca_us_equity_assets(registration_resolution=resolution)


def _execute_holdings_category_sync(routine: RoutineSpec) -> None:
    plan = build_holdings_asset_category_plan(
        etf_ticker=routine.etf_ticker,
        component_provider=routine.component_provider,
    )
    if plan.has_blockers():
        raise RuntimeError(
            "Refusing to create the holdings category because extracted holdings are incomplete "
            "or not fully registered uniquely in MainSequence."
        )
    sync_holdings_asset_category(
        etf_ticker=plan.etf_ticker,
        asset_uids=[
            plan.existing_asset_uids_by_symbol[symbol]
            for symbol in plan.component_symbols
            if symbol in plan.existing_asset_uids_by_symbol
        ],
    )


def _execute_etf_price_update(routine: RoutineSpec) -> None:
    node, _summary = build_stock_bars_node(
        asset_category_unique_identifier=None,
        tickers=[routine.etf_ticker],
        frequency_id=normalize_price_update_period(routine.etf_update_period),
        feed=DEFAULT_PRICE_UPDATE_FEED,
        adjustment=DEFAULT_PRICE_UPDATE_ADJUSTMENT,
        hash_namespace=None,
    )
    _run_stock_bars_node(node=node, force_update=True)


def _execute_category_price_update(routine: RoutineSpec) -> None:
    node, _summary = build_stock_bars_node(
        asset_category_unique_identifier=build_holdings_asset_category_unique_identifier(
            routine.etf_ticker
        ),
        tickers=None,
        frequency_id=routine.frequency_id,
        feed=routine.feed,
        adjustment=routine.adjustment,
        hash_namespace=None,
    )
    _run_stock_bars_node(node=node, force_update=True)


def _build_routine_steps(
    routine: RoutineSpec,
    *,
    dry_run: bool,
) -> list[RoutineStep]:
    del dry_run
    category_identifier = build_holdings_asset_category_unique_identifier(
        routine.etf_ticker
    )

    steps: list[RoutineStep] = []
    if routine.include_seed_registration:
        steps.append(
            RoutineStep(
                label="register_holdings_assets",
                preview_text=(
                    "service: expand_etf_seed_symbols -> "
                    "build_alpaca_us_equity_registration_plan -> "
                    "resolve_alpaca_us_equity_registration_plan -> "
                    "register_alpaca_us_equity_assets"
                ),
                runner=lambda routine=routine: _execute_seed_registration(routine),
            )
        )

    if routine.include_category_sync:
        steps.append(
            RoutineStep(
                label="sync_holdings_category",
                preview_text=(
                    "service: build_holdings_asset_category_plan -> "
                    "sync_holdings_asset_category"
                ),
                runner=lambda routine=routine: _execute_holdings_category_sync(routine),
            )
        )

    if routine.include_etf_price_update:
        steps.append(
            RoutineStep(
                label="update_etf_prices",
                preview_text=(
                    "service: build_stock_bars_node(ticker-scoped) -> _run_stock_bars_node"
                ),
                runner=lambda routine=routine: _execute_etf_price_update(routine),
            )
        )

    if routine.include_category_price_update:
        steps.append(
            RoutineStep(
                label="update_category_prices",
                preview_text=(
                    "service: build_stock_bars_node(category="
                    f"{category_identifier}) -> _run_stock_bars_node"
                ),
                runner=lambda routine=routine: _execute_category_price_update(routine),
            )
        )

    return steps


def run_routines(
    routines: list[RoutineSpec],
    *,
    dry_run: bool,
    continue_on_error: bool,
) -> int:
    failures: list[tuple[str, Exception]] = []
    total_steps = 0

    if dry_run:
        print("[dry-run] planned routine commands:")

    for routine in routines:
        steps = _build_routine_steps(routine, dry_run=dry_run)

        for step in steps:
            total_steps += 1
            if dry_run:
                print(f"  {routine.etf_ticker}: {step.label} -> {step.preview_text}")
                continue

            try:
                step.runner()
            except Exception as exc:
                failures.append((step.label, exc))
                if not continue_on_error:
                    print(
                        f"Failed after {total_steps} steps in routine {routine.etf_ticker}: "
                        f"{step.label}: {exc}"
                    )
                    return 1

    if failures:
        print("Completed with failures.")
        for label, exc in failures:
            print(f"  - {label}: {exc}")
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

    # Live routines register assets and write categories/bars through ms-markets MetaTables, so
    # attach the markets runtime once up front. Dry-run only prints previews and needs no backend.
    if not args.dry_run:
        from src.runtime import start_markets_engine

        start_markets_engine()

    return run_routines(
        specs,
        dry_run=args.dry_run,
        continue_on_error=args.continue_on_error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
