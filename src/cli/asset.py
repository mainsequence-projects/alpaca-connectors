from __future__ import annotations

import argparse
import json

from src.assets import (
    build_alpaca_us_equity_registration_plan,
    register_alpaca_us_equity_assets,
    resolve_alpaca_us_equity_registration_plan,
)
from src.universes import (
    SUPPORTED_COMPONENT_PROVIDERS,
    EtfExpansionRequest,
    expand_etf_seed_symbols,
)


def configure_register_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Register Alpaca US equity assets as MainSequence public assets using "
        "OpenFIGI classification."
    )
    symbol_group = parser.add_mutually_exclusive_group()
    symbol_group.add_argument(
        "--symbols",
        help="Comma-separated list of exact symbols to register. No component extraction is performed.",
    )
    symbol_group.add_argument(
        "--seed-tickers",
        help="Comma-separated list of seed tickers for component extraction.",
    )
    parser.add_argument(
        "--component-provider",
        choices=list(SUPPORTED_COMPONENT_PROVIDERS),
        help="Component extraction provider. Required when --seed-tickers is used.",
    )
    parser.add_argument(
        "--include-non-tradable",
        action="store_true",
        help="Include active Alpaca assets that are not tradable.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the MainSequence registration after printing the plan.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds for OpenFIGI and MainSequence requests.",
    )
    parser.set_defaults(handler=run_register_command)


def _parse_symbols(raw_symbols: str | None) -> list[str] | None:
    if not raw_symbols:
        return None
    symbols = [symbol.strip().upper() for symbol in raw_symbols.split(",") if symbol.strip()]
    return symbols or None


def _validate_registration_args(args: argparse.Namespace) -> None:
    if args.seed_tickers and not args.component_provider:
        raise SystemExit("--component-provider is required when --seed-tickers is used.")
    if args.component_provider and not args.seed_tickers:
        raise SystemExit("--seed-tickers is required when --component-provider is used.")


def _resolve_registration_symbols(
    args: argparse.Namespace,
) -> tuple[list[str] | None, object | None]:
    direct_symbols = _parse_symbols(args.symbols)
    if direct_symbols is not None:
        return direct_symbols, None

    if not args.seed_tickers:
        return None, None

    expansion_result = expand_etf_seed_symbols(
        EtfExpansionRequest(
            seed_tickers=_parse_symbols(args.seed_tickers) or [],
            component_provider=args.component_provider,
            timeout=args.timeout,
        )
    )
    return expansion_result.symbols_for_registration, expansion_result


def _build_registration_summary(plan, resolution, expansion_result) -> dict[str, object]:
    summary: dict[str, object] = {**plan.summary(), **resolution.summary()}
    if expansion_result is None:
        return summary

    summary.update(
        {
            "seed_symbols": expansion_result.universe.seed_symbols,
            "component_provider": expansion_result.component_provider,
            "expanded_candidate_symbols": expansion_result.universe.expanded_symbols,
            "expanded_component_symbols_by_seed": (
                expansion_result.universe.component_symbols_by_seed
            ),
            "unsupported_seed_symbols": expansion_result.universe.unsupported_seed_symbols,
        }
    )
    return summary


def run_register_command(args: argparse.Namespace) -> int:
    _validate_registration_args(args)
    symbols, expansion_result = _resolve_registration_symbols(args)

    # Resolving existing assets + executing registration go through ms-markets MetaTables.
    from src.runtime import start_markets_engine

    start_markets_engine()

    plan = build_alpaca_us_equity_registration_plan(
        symbols=symbols,
        include_non_tradable=args.include_non_tradable,
        timeout=args.timeout,
    )
    resolution = resolve_alpaca_us_equity_registration_plan(
        plan,
        timeout=args.timeout,
    )

    print("Planned Alpaca US equity registration")
    summary = _build_registration_summary(plan, resolution, expansion_result)
    print(json.dumps(summary, indent=2, sort_keys=True))

    if plan.unresolved_symbols:
        print(
            "Strict FIGI filter: these symbols were not eligible for registration because no FIGI was found:"
        )
        for symbol in plan.unresolved_symbols:
            warning = plan.warnings_by_symbol.get(symbol)
            if warning:
                print(f"  - {symbol}: {warning}")
            else:
                print(f"  - {symbol}")

    if not args.execute:
        print("Dry run only. Pass --execute to register the missing assets.")
        return 0

    print("Executing registration against MainSequence...")
    results = register_alpaca_us_equity_assets(
        registration_resolution=resolution,
        timeout=args.timeout,
    )
    print(
        json.dumps(
            {
                # Values are ms-markets asset uid strings (JSON-safe).
                "resolved_assets": results["assets"],
                "existing_assets": results["existing_assets"],
                "created_assets": results["created_assets"],
                "unresolved_symbols": results["unresolved_symbols"],
                "not_registered_missing_figi_symbols": results[
                    "not_registered_missing_figi_symbols"
                ],
                "not_registered_missing_alpaca_symbols": results[
                    "not_registered_missing_alpaca_symbols"
                ],
                "warnings_by_symbol": results["warnings_by_symbol"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0
