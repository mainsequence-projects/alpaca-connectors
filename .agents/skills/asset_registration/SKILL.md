---
name: alpaca-asset-registration
description: Use this skill when the task is about the repository's supported asset-registration workflow. This skill owns the strict Alpaca plus OpenFIGI registration path exposed through `alpaca-connectors asset register`.
---

# Alpaca Asset Registration

## Overview

Use this skill when the task is about registering Alpaca US equity assets into Main Sequence.

The supported operator surface is:

- `alpaca-connectors asset register`

This workflow is intentionally strict:

- the symbol must exist in Alpaca
- the symbol must resolve to a FIGI
- missing or unresolved symbols are reported and not registered

## This Skill Can Do

- explain or update the asset-registration CLI flow
- keep the registration behavior thinly orchestrated through `src/cli/asset.py`
- change the reusable registration logic under `src/assets/alpaca_us_equities.py`
- keep registration docs aligned with the strict FIGI-backed flow

## This Skill Must Not Claim

- custom-asset fallback registration behavior
- that missing Alpaca symbols or missing FIGIs can still be forced through the workflow
- ownership of ETF category sync or stock-bar publishing

## Supported Inputs

- exact symbols via `--symbols`
- expanded ETF seeds via `--seed-tickers` plus `--component-provider`
- optional `--include-non-tradable`
- dry run by default; `--execute` for writes

## Examples

- `alpaca-connectors asset register --symbols NVDA,AAPL`
- `alpaca-connectors asset register --seed-tickers IVV --component-provider ishares`
- `alpaca-connectors asset register --symbols BRKB --execute`
