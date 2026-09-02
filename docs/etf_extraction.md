# ETF Holdings Extraction Boundary

## Purpose

ETF holdings extraction is no longer implemented inside this repository. Provider parsing,
provider URL handling, holdings models, and ms-markets category primitives are delegated to the
external [`mainsequence-projects/etfholdingextractor`](https://github.com/mainsequence-projects/etfholdingextractor)
package, imported as `etfhextractor`.

This repository owns only Alpaca-specific orchestration:

- expand ETF seed tickers before Alpaca registration
- preserve the CLI/API summary shape expected by this project
- keep project-specific ETF provider defaults and discovery lists
- call `etfhextractor` to plan and sync holdings-backed `AssetCategory` rows

## Local Adapter

Main module:

- `src/etf_holdings.py`

The adapter exposes:

- `EtfExpansionRequest`
- `EtfExpansionResult`
- `expand_etf_seed_symbols(...)`
- `infer_holdings_component_provider(...)`
- `build_holdings_asset_category_unique_identifier(...)`
- `build_holdings_asset_category_plan(...)`
- `sync_holdings_asset_category(...)`

It also carries project discovery constants:

- `SUPPORTED_COMPONENT_PROVIDERS`
- `ETF_PROVIDER_MAP_NORMALIZED`
- `ETFS_MAIN_TICKERS`
- `MAG_7_CATEGORY_SYMBOLS`

## Dependency Direction

```text
alpaca-connectors/src -> etfhextractor -> ms-markets -> mainsequence
```

Do not add provider parser code back into this repository. If provider behavior must change,
change `etfhextractor` and consume the updated package here.

## Seed Expansion Flow

Used by:

```bash
alpaca-connectors asset register --seed-tickers IVV --component-provider ishares
```

Flow:

1. CLI/API builds `EtfExpansionRequest`
2. `src/etf_holdings.py` validates the provider against `etfhextractor`
3. `ETFHoldingsReader.read_ticker(...)` reads provider holdings
4. `derive_component_symbols_from_holdings(...)` returns component symbols
5. the Alpaca registration planner receives explicit symbols only

Alpaca registration still owns Alpaca availability and FIGI checks. ETF extraction does not.

## Holdings Category Flow

Used by:

```bash
alpaca-connectors holdings-category create --etf-ticker IVV
alpaca-connectors holdings-category create --etf-ticker IVV --execute
```

Flow:

1. provider is supplied or inferred from `ETF_PROVIDER_MAP_NORMALIZED`
2. `etfhextractor.build_holdings_asset_category_plan(...)` extracts component symbols
3. ticker-to-asset resolution uses ms-markets snapshots/detail tables from `etfhextractor`
4. missing or ambiguous registered assets block sync
5. `etfhextractor.sync_holdings_asset_category(...)` replaces `HOLDINGS__<ETF>` memberships

Members are ms-markets asset UIDs, not old integer ids.

## External CLI Reference

`etfhextractor` also ships its own CLI for direct extraction and category workflows:

```bash
etfh extract-ticker --provider ishares --ticker IVV
etfh category-sync --ticker IVV --provider ishares
```

The `alpaca-connectors` CLI remains the supported operator surface for this repository because it
combines ETF extraction with Alpaca registration, category sync, bars updates, and project-specific
defaults.
