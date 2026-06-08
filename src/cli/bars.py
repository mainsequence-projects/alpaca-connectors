from __future__ import annotations

import argparse
import json
from typing import Any

from src.assets.alpaca_us_equities import (
    _resolve_requested_symbols_to_alpaca_assets,
    fetch_alpaca_us_equities,
)
from src.data_nodes.alpaca_bars_support import normalize_frequency_id

DEFAULT_PRICE_UPDATE_FEED = "sip"
DEFAULT_PRICE_UPDATE_ADJUSTMENT = "all"
PRICE_UPDATE_ACTION_ALIASES = ("update-prices", "update_prices")


def parse_tickers(raw_value: str) -> list[str]:
    tickers = [value.strip().upper() for value in raw_value.split(",") if value.strip()]
    if not tickers:
        raise ValueError("At least one ticker must be provided.")
    return list(dict.fromkeys(tickers))


def normalize_price_update_period(period: str) -> str:
    normalized_period = period.strip().lower()
    aliases = {
        "daily": "1d",
        "day": "1d",
        "hourly": "1h",
        "hour": "1h",
        "minute": "1m",
        "minutely": "1m",
    }
    return normalize_frequency_id(aliases.get(normalized_period, normalized_period))


def resolve_registered_assets_from_tickers(
    *,
    tickers: list[str],
) -> tuple[list[str], dict[str, str]]:
    """Resolve tickers to registered ms-markets asset unique identifiers (strings).

    Preserves the three distinct strict errors: not available in Alpaca, available in Alpaca but
    not registered in ms-markets, and ambiguous in ms-markets. Asset lookup goes through
    ``OpenFigiDetails.ticker`` (provider symbols no longer live on the asset row).
    """
    from src.assets.resolution import assets_for_ticker

    requested_tickers = parse_tickers(",".join(tickers))
    alpaca_assets = fetch_alpaca_us_equities(include_non_tradable=False)
    resolved_alpaca_assets, missing_alpaca_tickers, requested_symbol_aliases = (
        _resolve_requested_symbols_to_alpaca_assets(
            alpaca_assets=alpaca_assets,
            requested_symbols=requested_tickers,
        )
    )
    if missing_alpaca_tickers:
        raise RuntimeError(
            "These tickers are not available in Alpaca: "
            f"{sorted(missing_alpaca_tickers)!r}"
        )

    resolved_alpaca_symbols = [asset.symbol for asset in resolved_alpaca_assets]
    assets_by_ticker: dict[str, list[Any]] = {
        ticker: assets_for_ticker(ticker) for ticker in resolved_alpaca_symbols
    }

    missing_platform_tickers = sorted(
        ticker for ticker in resolved_alpaca_symbols if not assets_by_ticker[ticker]
    )
    if missing_platform_tickers:
        raise RuntimeError(
            "These tickers are available in Alpaca but not registered in MainSequence: "
            f"{missing_platform_tickers!r}"
        )

    ambiguous_platform_tickers = sorted(
        ticker for ticker, assets in assets_by_ticker.items() if len(assets) != 1
    )
    if ambiguous_platform_tickers:
        raise RuntimeError(
            "These tickers resolved ambiguously in MainSequence: "
            f"{ambiguous_platform_tickers!r}"
        )

    resolved_unique_identifiers = [
        assets_by_ticker[ticker][0].unique_identifier for ticker in resolved_alpaca_symbols
    ]
    return resolved_unique_identifiers, requested_symbol_aliases


def build_stock_bars_node(
    *,
    asset_category_unique_identifier: str | None,
    tickers: list[str] | None,
    frequency_id: str,
    feed: str,
    adjustment: str,
    hash_namespace: str | None,
) -> tuple[Any, dict[str, Any]]:
    # Storage-first: building/running the node attaches to the migrated+registered storage table,
    # so the markets runtime must be initialized first (idempotent/cached).
    from src.runtime import start_markets_engine

    start_markets_engine()

    requested_tickers = list(tickers) if tickers is not None else None
    resolved_unique_identifiers = None
    requested_symbol_aliases: dict[str, str] = {}
    if requested_tickers is not None:
        resolved_unique_identifiers, requested_symbol_aliases = (
            resolve_registered_assets_from_tickers(tickers=requested_tickers)
        )

    config_kwargs: dict[str, Any] = {
        "frequency_id": frequency_id,
        "feed": feed,
        "adjustment": adjustment,
    }
    if asset_category_unique_identifier:
        config_kwargs["asset_category_unique_identifier"] = asset_category_unique_identifier
    else:
        config_kwargs["asset_list"] = resolved_unique_identifiers

    from src.data_nodes import AlpacaStockBarsConfig, AlpacaStockBarsNode

    config = AlpacaStockBarsConfig(**config_kwargs)
    node = AlpacaStockBarsNode(
        config=config,
        hash_namespace=hash_namespace,
    )

    # get_asset_list() now returns asset unique-identifier strings (not asset objects).
    node_assets = node.get_asset_list()
    summary = {
        "asset_category_unique_identifier": asset_category_unique_identifier,
        "requested_tickers": requested_tickers,
        "requested_symbol_aliases": requested_symbol_aliases,
        "asset_count": len(node_assets),
        "frequency_id": node.frequency_id,
        "feed": node.feed,
        "adjustment": node.adjustment,
        "hash_namespace": node.hash_namespace or None,
        "table_identifier": node.storage_table.__metatable_identifier__,
        "storage_hash": node.storage_hash,
        "update_hash": node.update_hash,
        "resolved_asset_unique_identifiers_sample": list(node_assets)[:20],
    }
    return node, summary


def _print_stock_bars_run_summary(summary: dict[str, Any]) -> None:
    print("Planned Alpaca stock bars run")
    print(json.dumps(summary, indent=2, sort_keys=True))


def _run_stock_bars_node(
    *,
    node: Any,
    force_update: bool,
) -> None:
    error_on_last_update, update_result = node.run(
        debug_mode=True,
        force_update=force_update,
    )
    if update_result is None:
        print("Run completed with no returned frame.")
        return

    print("Run completed")
    print(
        json.dumps(
            {
                "error_on_last_update": bool(error_on_last_update),
                "rows_persisted": int(len(update_result)),
                "columns": list(update_result.columns),
            },
            indent=2,
            sort_keys=True,
        )
    )


def configure_run_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Build and run Alpaca stock bars for a MainSequence asset category or a strict list "
        "of registered tickers. The table identity is determined by frequency_id, feed, and "
        "adjustment."
    )
    scope_group = parser.add_mutually_exclusive_group(required=True)
    scope_group.add_argument(
        "--asset-category-unique-identifier",
        help="MainSequence AssetCategory unique_identifier, for example HOLDINGS__IVV.",
    )
    scope_group.add_argument(
        "--tickers",
        help="Comma-separated tickers to resolve strictly against Alpaca and MainSequence, for example NVDA or AAPL,MSFT.",
    )
    parser.add_argument(
        "--frequency-id",
        default="1d",
        help="Alpaca bar frequency, for example 1d, 1h, 15m, 5m, or 1m.",
    )
    parser.add_argument(
        "--feed",
        default="iex",
        help="Alpaca stock market data feed, for example iex or sip.",
    )
    parser.add_argument(
        "--adjustment",
        default="raw",
        help="Alpaca adjustment mode, for example raw, split, dividend, or all.",
    )
    parser.add_argument(
        "--hash-namespace",
        default=None,
        help="Optional hash namespace used to isolate the DataNode run.",
    )
    parser.add_argument(
        "--force-update",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Force one DataNode update cycle even if no new range is detected.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the resolved DataNode plan without executing node.run().",
    )
    parser.set_defaults(handler=run_bars_command)


def run_bars_command(args: argparse.Namespace) -> int:
    requested_tickers = parse_tickers(args.tickers) if args.tickers else None
    node, summary = build_stock_bars_node(
        asset_category_unique_identifier=args.asset_category_unique_identifier,
        tickers=requested_tickers,
        frequency_id=args.frequency_id,
        feed=args.feed,
        adjustment=args.adjustment,
        hash_namespace=args.hash_namespace,
    )
    _print_stock_bars_run_summary(summary)
    if args.plan_only:
        print("Plan only. Re-run without --plan-only to execute node.run().")
        return 0

    _run_stock_bars_node(
        node=node,
        force_update=args.force_update,
    )
    return 0


def build_asset_price_update_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alpaca-connectors asset <ticker> update-prices <period>",
        description=(
            "Resolve a registered MainSequence asset from a ticker and update its Alpaca "
            "stock bars through the DataNode."
        ),
    )
    parser.add_argument(
        "ticker",
        help="Registered Alpaca/MainSequence ticker to update, for example IVV or NVDA.",
    )
    parser.add_argument(
        "asset_action",
        choices=PRICE_UPDATE_ACTION_ALIASES,
        help="Asset action to run.",
    )
    parser.add_argument(
        "period",
        help="Price update cadence, for example daily, hourly, 15m, 1h, or 1d.",
    )
    parser.add_argument(
        "--feed",
        default=DEFAULT_PRICE_UPDATE_FEED,
        help="Alpaca stock market data feed, defaulting to sip for the shorthand asset flow.",
    )
    parser.add_argument(
        "--adjustment",
        default=DEFAULT_PRICE_UPDATE_ADJUSTMENT,
        help="Alpaca adjustment mode, defaulting to all for the shorthand asset flow.",
    )
    parser.add_argument(
        "--hash-namespace",
        default=None,
        help="Optional hash namespace used to isolate the DataNode run.",
    )
    parser.add_argument(
        "--force-update",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Force one DataNode update cycle even if no new range is detected.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the resolved DataNode plan without executing node.run().",
    )
    return parser


def run_asset_price_update_command(argv: list[str]) -> int:
    parser = build_asset_price_update_parser()
    args = parser.parse_args(argv)
    frequency_id = normalize_price_update_period(args.period)
    node, summary = build_stock_bars_node(
        asset_category_unique_identifier=None,
        tickers=[args.ticker],
        frequency_id=frequency_id,
        feed=args.feed,
        adjustment=args.adjustment,
        hash_namespace=args.hash_namespace,
    )
    summary["requested_period"] = args.period
    _print_stock_bars_run_summary(summary)
    if args.plan_only:
        print("Plan only. Re-run without --plan-only to execute node.run().")
        return 0

    _run_stock_bars_node(
        node=node,
        force_update=args.force_update,
    )
    return 0
