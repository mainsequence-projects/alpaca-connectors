---
name: alpaca-stock-bars
description: Use this skill when the task is about planning or running the repository's supported Alpaca stock-bar update workflows through the CLI and reusable DataNode modules.
---

# Alpaca Stock Bars

## Overview

Use this skill when the task is about planning or executing stock-bar updates.

Supported operator surfaces:

- `alpaca-connectors market-data update`
- `alpaca-connectors market-data bar-configuration`
- `alpaca-connectors asset <ticker> update_prices <period>`

These flows build and run the reusable `AlpacaStockBarsNode`.

## This Skill Can Do

- explain or update the CLI orchestration around stock-bar runs
- use `src.market_data` as the public capability boundary and change its underlying reusable
  storage or DataNode implementation only when required
- keep the single-asset shorthand and stored-configuration runner aligned with current project
  validation rules
- keep scheduled-job entrypoints aligned with the supported runners

## This Skill Must Not Claim

- that unregistered Main Sequence assets can be updated directly
- that an update can execute without a registered Account whose stored Secret names resolve
- ownership of asset registration or holdings-category extraction behavior

## Working Rules

- create and review a stored configuration before an update
- exactly one source is valid: explicit assets, an active universe, or recent account holdings
- account-holdings resolution uses the newest stored snapshot inside the inclusive trailing 30-day
  window and never captures a snapshot as a side effect
- frequency/feed/adjustment resolve one migrated output dataset and schema
- dry run is the default; use `--execute` only when a write is intended
- require a stored configuration UID; never accept a dataset UID or runtime scope override
- keep the shared table identity keyed by `frequency_id`, `feed`, and `adjustment`
- load Asset identity/provider details with set-based governed queries and fetch bars in symbol
  batches; never issue one Main Sequence lookup or Alpaca bars request per Asset

## Examples

- `alpaca-connectors market-data bar-configuration create --name "Daily account holdings" --account-uid <ACCOUNT_UID> --asset-source account_holdings --frequency 1d --feed sip --adjustment all`
- `alpaca-connectors market-data update --configuration-uid <CONFIGURATION_UID>`
- `alpaca-connectors market-data update --configuration-uid <CONFIGURATION_UID> --execute`
- `alpaca-connectors asset IVV update_prices daily --configuration-uid <CONFIGURATION_UID>`
