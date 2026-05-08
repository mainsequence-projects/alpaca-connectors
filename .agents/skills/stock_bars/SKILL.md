---
name: alpaca-stock-bars
description: Use this skill when the task is about planning or running the repository's supported Alpaca stock-bar update workflows through the CLI and reusable DataNode modules.
---

# Alpaca Stock Bars

## Overview

Use this skill when the task is about planning or executing stock-bar updates.

Supported operator surfaces:

- `alpaca-connectors bars run`
- `alpaca-connectors asset <ticker> update_prices <period>`

These flows build and run the reusable `AlpacaStockBarsNode`.

## This Skill Can Do

- explain or update the CLI orchestration around stock-bar runs
- change reusable behavior under `src/data_nodes/alpaca_bars.py` and
  `src/data_nodes/alpaca_bars_support.py`
- keep the single-asset shorthand and generic category/ticker runner aligned with current project
  defaults and validation rules
- keep scheduled-job entrypoints aligned with the supported runners

## This Skill Must Not Claim

- that unregistered Main Sequence assets can be updated directly
- that the generic runner uses the same defaults as the single-asset shorthand unless flags are
  set explicitly
- ownership of asset registration or holdings-category extraction behavior

## Working Rules

- single-asset shorthand defaults to `feed=sip` and `adjustment=all`
- generic `bars run` defaults to `feed=iex` and `adjustment=raw`
- use `--plan-only` when the task only needs the resolved node/table/hash plan
- keep the shared table identity keyed by `frequency_id`, `feed`, and `adjustment`

## Examples

- `alpaca-connectors asset IVV update_prices daily --plan-only`
- `alpaca-connectors bars run --tickers NVDA,AAPL --frequency-id 1d --feed sip --adjustment all`
- `alpaca-connectors bars run --asset-category-unique-identifier HOLDINGS__IVV --frequency-id 1d --feed sip --adjustment all`
