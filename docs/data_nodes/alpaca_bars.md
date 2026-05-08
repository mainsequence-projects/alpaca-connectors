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

## Data Contract

The node publishes an asset-indexed table with:

- index: `(time_index, unique_identifier)`
- columns:
  - `open`
  - `high`
  - `low`
  - `close`
  - `volume`
  - `trade_count`
  - `vwap`

`unique_identifier` is always the MainSequence asset `unique_identifier`.

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
append/update the same DataNode table.

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
  --feed sip \
  --adjustment all
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
