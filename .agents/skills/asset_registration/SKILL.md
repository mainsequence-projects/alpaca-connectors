---
name: alpaca-asset-registration
description: Use this skill when the task is about the repository's supported asset-registration workflow. This skill owns provider-native Alpaca UUID registration with optional OpenFIGI enrichment through `alpaca-connectors asset register`.
---

# Alpaca Asset Registration

## Overview

Use this skill when the task is about registering Alpaca US equity assets into Main Sequence.

The supported operator surface is:

- `alpaca-connectors asset register`

This workflow has one authoritative identity contract:

- the caller selects a registered Alpaca account by `--account-uid`
- provider credentials resolve only from the Main Sequence Secret names stored on that account
- the symbol must exist in Alpaca
- Alpaca must provide an immutable asset UUID
- `Asset.unique_identifier` is `ALPACA::<alpaca_asset_uuid>`
- every connector-owned Asset has a required `AlpacaAssetDetails` row keyed by `Asset.uid`
- OpenFIGI details are optional enrichment and never block registration
- symbols missing from Alpaca are reported and not registered

## This Skill Can Do

- explain or update the asset-registration CLI flow
- keep the registration behavior thinly orchestrated through `src/cli/asset.py`
- change the reusable registration logic under `src/assets/alpaca_us_equities.py`
- maintain the project-owned `AlpacaAssetDetails` schema and migration wiring
- keep CLI, API, application progress, tests, and docs aligned with provider-native identity

## This Skill Must Not Claim

- custom-asset fallback registration behavior
- that missing Alpaca symbols can be forced through the workflow
- that a FIGI is required or that FIGI is the canonical Asset identifier
- ownership of ETF category membership refresh or stock-bar publishing
- that the Assets workflow expands ETF seeds; provider-derived constituents belong to Universe Run

## Identity And Enrichment

Registration must:

1. resolve the input symbol against Alpaca;
2. validate Alpaca's asset UUID;
3. resolve or upsert `Asset(unique_identifier="ALPACA::<uuid>")`;
4. upsert required `AlpacaAssetDetails` from the Alpaca response;
5. optionally upsert the existing ms-markets `OpenFigiDetails` row when enrichment succeeds.

Universe-sized registration must use the ms-markets bulk MetaTable upsert operation for each row
type and publish all AssetSnapshot rows through one `AssetSnapshot.set_snapshots([...]).run(...)`
execution. Never launch one TimeIndexTableUpdater run per constituent.

OpenFIGI timeouts, connectivity failures, invalid responses, and unmatched symbols are explicit
warnings. They must not set `can_register=false`, omit the Alpaca asset, or fail execution.

## Supported Inputs

- a required registered Alpaca account via `--account-uid`
- required exact symbols via `--symbols`
- Alpaca lifecycle fields such as `status` and `tradable` are persisted provider facts, never
  registration eligibility gates; exact symbol sets resolve through one all-status bulk catalog
  request, never one provider request per symbol
- dry run by default; `--execute` for writes

## Examples

- `alpaca-connectors asset register --account-uid <ACCOUNT_UID> --symbols NVDA,AAPL`
- `alpaca-connectors asset register --account-uid <ACCOUNT_UID> --symbols BRKB --execute`

For provider-derived constituents, configure an `AssetUniverse` and Run it. That workflow calls the
same registration service with the extracted explicit symbols and the account selected for that
Run. The account is not stored on the Universe.
