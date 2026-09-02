# Alpaca Stock Bars DataNode

## Goal

Publish Alpaca stock OHLCV bars into MainSequence for either:

- a registered `AssetCategory`
- a strict list of registered tickers

Main modules:

- `src/data_nodes/alpaca_bars.py`
- `src/data_nodes/alpaca_bars_support.py`

Primary runner:

- `src/cli/bars.py`

## Architecture (storage-first, ms-markets)

`AlpacaStockBarsNode` is an `msm.data_nodes.assets.AssetIndexedDataNode` (ms-markets), not the
old `mainsequence.tdag.DataNode`. The output schema is **storage-first**: it lives on a
SQLAlchemy storage class (`src/markets_storage/alpaca_bars.py`), not on the config. The config
(`AlpacaStockBarsConfig`) carries only updater scope (`frequency_id`/`feed`/`adjustment`,
`asset_list`, `asset_category_unique_identifier`).

The base class supplies universe scoping and the per-asset incremental `update_statistics`
(`get_asset_update_range_map_great_or_equal`) that the node used to compute by hand.

## Data Contract

The node publishes an asset-indexed table with:

- index: `(time_index, asset_identifier)` (the dimension constant is
  `msm.settings.ASSET_IDENTIFIER_DIMENSION == "asset_identifier"`)
- columns: `open`, `high`, `low`, `close`, `volume`, `trade_count`, `vwap`

`asset_identifier` is always the ms-markets `Asset.unique_identifier`. The storage table declares
a SQLAlchemy `ForeignKey` from `asset_identifier` to `AssetTable.unique_identifier`.

## Hashed Fields

These fields are part of dataset identity:

- `frequency_id`
- `feed`
- `adjustment`

This prevents mixing `sip` and `iex`, or adjusted and unadjusted bars, in the same dataset.

## Shared Table Identity

The table identity is shared across scopes and depends only on:

- `frequency_id`
- `feed`
- `adjustment`

That means ticker runs and category runs with the same values for those fields
append/update the same storage table. Each `(frequency_id, feed, adjustment)` triple maps to one
storage class via `src/markets_storage/alpaca_bars.py::storage_for(...)`; its
`__metatable_identifier__` is the legacy string `alpaca_stock_bars_<freq>_<feed>_<adjustment>`
(e.g. `alpaca_stock_bars_1d_sip_all`), so the published DataNode identifier and the chart API
contract are preserved. Registered triples today:

- `1d/sip/all` -> `alpaca_stock_bars_1d_sip_all`
- `1d/iex/raw` -> `alpaca_stock_bars_1d_iex_raw`

Each storage class declares an explicit physical table name with the triple in the concept segment
(for example `bars_1d_iex_raw`) and `__cadence__ = "1d"`. Extra storage identity components include
only the non-cadence variant fields (`feed` and `adjustment`); cadence owns the frequency.
Together, these prevent identical OHLCV schemas from collapsing to the same storage identity and
record the table cadence for downstream platform consumers. Add a storage class + migration for any
new triple before publishing it.

## Prerequisites (storage-first)

Two steps that did not exist under the old implicit-registration model are now **required**:

1. **Migration** — the storage table must be created/registered by the SDK migration provider
   before any write: `mainsequence migrations upgrade --provider src.migrations:migration head`.
2. **Runtime attach** — every process calls `src.runtime.start_markets_engine()` (wrapping
   `msm.start_engine(models=[...])`) once before building/running the node. The CLI/jobs do this
   for you. Without it, row/node operations raise `RuntimeError`.

## Runner Scope Modes

### Asset category mode

```bash
alpaca-connectors bars run \
  --asset-category-unique-identifier HOLDINGS__IVV \
  --frequency-id 1d \
  --feed sip \
  --adjustment all
```

In category mode, the category is assumed to already exist. The Alpaca bars flow
loads and consumes the category; it does not create or refresh it.

### Ticker mode

```bash
alpaca-connectors bars run \
  --tickers NVDA \
  --frequency-id 1d \
  --feed iex \
  --adjustment raw
```

### Shorthand asset mode

```bash
alpaca-connectors asset IVV update_prices daily
```

The shorthand resolves the registered MainSequence asset from ticker `IVV`, uses the
project daily price defaults of `feed=sip` and `adjustment=all`, builds the
`AlpacaStockBarsConfig`, and runs the DataNode for that single asset.

Ticker mode is strict. Every ticker must exist in Alpaca and also exist as exactly one registered MainSequence asset.

## Time Index Decision

For intraday bars, `time_index` is the right edge of the interval.

For `1d` bars, the project normalizes `time_index` to:

- `16:00 America/New_York`
- on the same session date

This is a project normalization convention, not a claim that Alpaca guarantees that exact vendor-native final observation timestamp in every case.

## Incomplete-Bar Handling

The updater filters out the current incomplete period before persistence.

## Credentials

The node resolves Alpaca credentials via:

1. environment variables
2. MainSequence secrets fallback

If neither exists, it fails fast.
