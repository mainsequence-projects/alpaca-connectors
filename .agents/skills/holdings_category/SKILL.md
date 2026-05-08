---
name: alpaca-holdings-category
description: Use this skill when the task is about creating or refreshing holdings-backed Main Sequence asset categories from ETF constituents through the supported project CLI.
---

# Holdings Category Sync

## Overview

Use this skill when the task is about ETF constituent universes and holdings-backed
`AssetCategory` sync.

The supported operator surface is:

- `alpaca-connectors holdings-category create`

This workflow builds or refreshes categories such as `HOLDINGS__IVV` from the ETF extraction
package and existing Main Sequence asset registration state.

## This Skill Can Do

- explain or update the holdings-category CLI flow
- change the reusable planning and sync logic under `etf_extraction/holdings_categories.py`
- keep ETF provider inference and category naming aligned with the current project rules
- keep docs aligned with the strict category-sync behavior

## This Skill Must Not Claim

- that missing registered symbols can be skipped silently
- that ambiguous Main Sequence ticker matches are acceptable
- ownership of the Alpaca registration flow or stock-bar publishing itself

## Working Rules

- dry run is the default
- execute only after extracted holdings resolve uniquely in Main Sequence
- if symbols are missing, register them first instead of weakening the category-sync rules

## Examples

- `alpaca-connectors holdings-category create --etf-ticker IVV`
- `alpaca-connectors holdings-category create --etf-ticker QQQ --component-provider invesco`
- `alpaca-connectors holdings-category create --etf-ticker IVV --execute`
