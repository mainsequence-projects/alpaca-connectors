---
name: alpaca-holdings-category
description: Use this skill when the task is about creating or refreshing holdings-backed Main Sequence asset universes through the supported project CLI.
---

# Holdings-Backed Universe Sync

## Overview

Use this skill when the task is about ETF constituent universes and holdings-backed
`AssetCategory` sync.

The canonical operator surface is:

- `alpaca-connectors universe sync`

This workflow builds or refreshes categories such as `HOLDINGS__IVV` from a durable,
user-maintained `UniverseSource`, the external `etfhextractor` package, and existing Main Sequence
asset registration state.

## This Skill Can Do

- explain or update the holdings-backed universe CLI flow
- change the local orchestration adapter in `src/universes/`
- rely on `etfhextractor` for provider extraction and ms-markets holdings-category primitives
- keep source-based extraction and category naming aligned with the current project rules
- keep docs aligned with the strict category-sync behavior

## This Skill Must Not Claim

- that missing registered symbols can be skipped silently
- that ambiguous Main Sequence ticker matches are acceptable
- ownership of the Alpaca registration flow or stock-bar publishing itself

## Working Rules

- dry run is the default
- select extraction input by `UniverseSource.uid`; do not infer a provider from a Python map
- execute only after extracted holdings resolve uniquely in Main Sequence
- if symbols are missing, register them first instead of weakening the category-sync rules

## Examples

- `alpaca-connectors universe-source list`
- `alpaca-connectors universe-source preview <SOURCE_UID>`
- `alpaca-connectors universe sync --source-uid <SOURCE_UID>`
- `alpaca-connectors universe sync --source-uid <SOURCE_UID> --execute`
