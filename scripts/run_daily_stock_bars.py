from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.assets.alpaca_us_equities import (
    _resolve_requested_symbols_to_alpaca_assets,
    fetch_alpaca_us_equities,
)


def _load_mainsequence_client() -> Any:
    import mainsequence.client as msc

    return msc


def _parse_tickers(raw_value: str) -> list[str]:
    tickers = [value.strip().upper() for value in raw_value.split(",") if value.strip()]
    if not tickers:
        raise ValueError("At least one ticker must be provided.")
    return list(dict.fromkeys(tickers))


def resolve_registered_assets_from_tickers(
    *,
    tickers: list[str],
) -> tuple[list[Any], dict[str, str]]:
    requested_tickers = _parse_tickers(",".join(tickers))
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
    msc = _load_mainsequence_client()
    matching_assets = msc.Asset.filter(current_snapshot__ticker__in=resolved_alpaca_symbols)
    assets_by_ticker: dict[str, list[Any]] = {}
    for asset in matching_assets:
        ticker = getattr(asset, "ticker", None)
        if not ticker:
            continue
        assets_by_ticker.setdefault(str(ticker).upper(), []).append(asset)

    missing_platform_tickers = sorted(
        ticker for ticker in resolved_alpaca_symbols if ticker not in assets_by_ticker
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

    resolved_assets = [assets_by_ticker[ticker][0] for ticker in resolved_alpaca_symbols]
    return resolved_assets, requested_symbol_aliases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build and run Alpaca stock bars for a MainSequence asset category. "
            "The table identity is determined by frequency_id, feed, and adjustment."
        ),
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
    return parser


def main(argv: list[str] | None = None) -> None:
    from src.data_nodes import AlpacaStockBarsConfig, AlpacaStockBarsNode

    parser = build_parser()
    args = parser.parse_args(argv)

    requested_tickers = _parse_tickers(args.tickers) if args.tickers else None
    resolved_assets = None
    requested_symbol_aliases: dict[str, str] = {}
    if requested_tickers is not None:
        resolved_assets, requested_symbol_aliases = resolve_registered_assets_from_tickers(
            tickers=requested_tickers,
        )

    config_kwargs = {
        "frequency_id": args.frequency_id,
        "feed": args.feed,
        "adjustment": args.adjustment,
    }
    if args.asset_category_unique_identifier:
        config_kwargs["asset_category_unique_identifier"] = args.asset_category_unique_identifier
    else:
        config_kwargs["asset_list"] = resolved_assets

    config = AlpacaStockBarsConfig(**config_kwargs)
    node = AlpacaStockBarsNode(
        config=config,
        hash_namespace=args.hash_namespace,
    )

    resolved_assets = node.get_asset_list()
    summary = {
        "asset_category_unique_identifier": args.asset_category_unique_identifier,
        "requested_tickers": requested_tickers,
        "requested_symbol_aliases": requested_symbol_aliases,
        "asset_count": len(resolved_assets),
        "frequency_id": node.frequency_id,
        "feed": node.feed,
        "adjustment": node.adjustment,
        "hash_namespace": node.hash_namespace or None,
        "table_identifier": (
            node.get_table_metadata().identifier if node.get_table_metadata() else None
        ),
        "storage_hash": node.storage_hash,
        "update_hash": node.update_hash,
        "resolved_asset_unique_identifiers_sample": [
            asset.unique_identifier for asset in resolved_assets
        ][:20],
    }
    print("Planned Alpaca stock bars run")
    print(json.dumps(summary, indent=2, sort_keys=True))

    error_on_last_update, update_result = node.run(
        debug_mode=True,
        force_update=args.force_update,
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


if __name__ == "__main__":
    main()
