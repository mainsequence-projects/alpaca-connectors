---
name: alpaca-holdings-category
description: Use this skill when the task is about creating or running registered holdings-backed Main Sequence Asset Universes through the supported project surfaces.
---

# Holdings-Backed Asset Universes

## Overview

Use this skill for ETF constituent universes and holdings-backed `AssetCategory` membership.

The canonical operator surface is:

- `alpaca-connectors universe run <UNIVERSE_UID>`

A registered `AssetUniverse` is the lifecycle resource for one ETF holdings source and its
materialized Asset membership. It has a required `source_uid` foreign key to one explicit `UniverseSource`
and a required `asset_category_uid` foreign key to the category it materializes. An Alpaca account
is selected as Run execution context only and is never stored or inferred on the Universe. Category
metadata is never universe configuration.

## This Skill Can Do

- explain or update explicit Asset Universe registration and Run behavior
- change the local orchestration adapter in `src/universes/`
- rely on `etfhextractor` for provider extraction
- keep source-based extraction and linked category membership aligned
- keep API, CLI, and docs aligned with strict membership validation

## This Skill Must Not Claim

- that a source preview creates, infers, or runs an Asset Universe
- that a ticker, provider, source, or category may be inferred
- that missing registered symbols block Run; they are registration work owned by Run
- that ambiguous Main Sequence ticker matches are acceptable
- ownership of Alpaca asset registration or stock-bar publishing itself

## Working Rules

- universe creation always receives an explicit name, symbol, and source URL
- creation stores real source and category relationships and performs no extraction
- source preview is read-only
- Run accepts `AssetUniverse.uid`; dry run is the default
- dry-run planning requires an explicit account UID, extracts holdings, and plans provider-native
  Alpaca registration through that execution account
- execute registers missing Alpaca-backed constituents before replacing membership
- symbols Alpaca cannot resolve block the Run and leave membership unchanged
- Run writes only to the category linked by `asset_category_uid`
- Run delegates category replacement to the `ms-markets>=1.0.3`
  `AssetCategory.replace_memberships` API, which bulk-upserts the desired memberships and removes
  stale rows in one delete; it never executes one membership operation per constituent
- Universe Run does not persist extracted weights; `AssetCategoryMembership` records membership
  only, and any weights published by the separate ETF portfolio signal workflow are not Universe
  state
- a referenced source cannot be deleted and its symbol cannot be changed
- a universe referenced by a bar configuration cannot be deleted

## Examples

- `alpaca-connectors universe-source list`
- `alpaca-connectors universe-source preview <SOURCE_UID>`
- `alpaca-connectors universe list`
- `alpaca-connectors universe get <UNIVERSE_UID>`
- `alpaca-connectors universe run <UNIVERSE_UID>`
- `alpaca-connectors universe run <UNIVERSE_UID> --execute`
