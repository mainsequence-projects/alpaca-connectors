# ETF Extraction Architecture

## Purpose

`etf_extraction/` is a standalone package for ETF holdings workflows.

It owns:

- provider-specific holdings extraction
- ETF seed expansion into component symbols
- ETF provider configuration and seed-universe data
- ETF-owned holdings `AssetCategory` planning and sync
- ETF-oriented tests

It does not own:

- Alpaca symbol validation
- Alpaca asset registration
- Alpaca bars `DataNode` execution
- generic CLI/runtime settings for the rest of the project

That separation is intentional. ETF holdings are a source dataset. Alpaca is one downstream consumer.

## Root Package

Repository path:

```text
etf_extraction/
```

Main modules:

- `etf_extraction/service.py`
- `etf_extraction/settings.py`
- `etf_extraction/holdings_categories.py`
- `etf_extraction/extractors/`
- `etf_extraction/data/seed_universes.yaml`
- `etf_extraction/tests/`

## Public Interfaces

### ETF expansion service

Defined in `etf_extraction/service.py`.

Primary API:

- `EtfExpansionRequest`
- `EtfExpansionResult`
- `expand_etf_seed_symbols(...)`

Responsibility:

- receive one or more seed tickers plus an explicit provider
- build the correct extractor
- return the expanded symbol universe

Output shape:

- `EtfExpansionResult.component_provider`
- `EtfExpansionResult.universe`
- `EtfExpansionResult.symbols_for_registration`

This service is independent of Alpaca. It only expands ETF seeds into symbols.

### Holdings category service

Defined in `etf_extraction/holdings_categories.py`.

Primary API:

- `build_holdings_asset_category_unique_identifier(...)`
- `infer_holdings_component_provider(...)`
- `build_holdings_asset_category_plan(...)`
- `resolve_existing_assets_by_ticker(...)`
- `sync_holdings_asset_category(...)`

Plan object:

- `HoldingsAssetCategoryPlan`

Sync result:

- `AssetCategorySyncResult`

Responsibility:

- infer or receive the ETF provider
- extract component symbols for one ETF
- check which symbols already map to existing MainSequence assets
- detect missing or ambiguous MainSequence ticker matches
- create or refresh the ETF-owned `AssetCategory`

Important boundary:

- this service does not call Alpaca
- this service does not require Alpaca tradability checks
- this service only depends on ETF extraction plus MainSequence asset/category state

## Provider Extraction Layer

Extractor implementations live under `etf_extraction/extractors/`.

Modules:

- `etf_extraction/extractors/common.py`
- `etf_extraction/extractors/registry.py`
- `etf_extraction/extractors/browser.py`
- `etf_extraction/extractors/ishares.py`
- `etf_extraction/extractors/invesco.py`
- `etf_extraction/extractors/vanguard.py`
- `etf_extraction/extractors/state_street.py`

The extractor layer owns:

- provider URL resolution
- provider response fetching
- provider-specific parsing
- normalization into `ExpandedSymbolUniverse`

The extractor layer does not own:

- MainSequence asset registration
- Alpaca API interaction
- `AssetCategory` sync

## Configuration And Seed Data

ETF-specific settings live in `etf_extraction/settings.py`.

This module owns:

- `SUPPORTED_COMPONENT_PROVIDERS`
- `ETF_EXTRACTION_DIR`
- `ETF_DATA_DIR`
- `SEED_UNIVERSES_PATH`
- `get_seed_universes()`
- `get_etf_provider_map()`
- `ETF_PROVIDER_MAP_NORMALIZED`
- provider source builders such as `get_ishares_holdings_source(...)`

Seed-universe data lives in:

```text
etf_extraction/data/seed_universes.yaml
```

That file is the source for:

- `etf_provider_map_normalized`
- ETF ticker groups such as `etfs_main_tickers`
- other ETF-owned symbol lists used by the extraction side

## Execution Flows

### Seed expansion only

Used when a caller needs component symbols but does not need category sync yet.

Flow:

1. caller creates `EtfExpansionRequest`
2. `expand_etf_seed_symbols(...)` validates the provider
3. the provider extractor expands the seed tickers
4. the caller receives explicit component symbols

Typical downstream consumer:

- `src/cli/asset.py`, which expands ETF seeds first and then passes explicit symbols into the Alpaca registration flow

### Holdings category creation

Used when a caller needs an ETF-owned `AssetCategory` such as `HOLDINGS__IVV`.

Flow:

1. ETF ticker is normalized
2. provider is inferred from `ETF_PROVIDER_MAP_NORMALIZED` or supplied explicitly
3. extractor expands the ETF into component symbols
4. MainSequence asset lookup checks whether those symbols already exist uniquely
5. `sync_holdings_asset_category(...)` creates or refreshes `HOLDINGS__<ETF>`

Important detail:

- this flow is about ETF membership
- it is not an Alpaca availability filter

If Alpaca later cannot use some members of the category, that is handled by Alpaca consumers, not by `etf_extraction/`

## Overlap With Alpaca

The overlap is intentionally narrow.

`etf_extraction/` produces:

- explicit component tickers
- ETF-owned holdings categories

`src/` consumes those outputs for Alpaca-specific work:

- `src/cli/asset.py` uses ETF expansion output before Alpaca registration planning
- `src/assets/alpaca_us_equities.py` handles Alpaca plus FIGI registration from explicit symbols only
- `src/data_nodes/alpaca_bars.py` consumes existing assets or categories and decides what Alpaca bars can publish

Dependency direction:

```text
etf_extraction -> explicit symbols or HOLDINGS__<ETF> category
src/ Alpaca code -> validates or consumes those outputs later
```

That means ETF extraction can be reasoned about independently from Alpaca runtime constraints.

## CLI Surfaces That Use ETF Extraction

ETF expansion for registration:

```bash
alpaca-connectors asset register --seed-tickers IVV --component-provider ishares
```

ETF category planning and sync:

```bash
alpaca-connectors holdings-category create --etf-ticker IVV
alpaca-connectors holdings-category create --etf-ticker IVV --execute
```

These commands are orchestration layers. The ETF logic itself lives under `etf_extraction/`.

## Tests

ETF-specific tests live under:

```text
etf_extraction/tests/
```

Current coverage areas:

- extractor behavior
- ETF holdings category planning
- ETF routine orchestration

This keeps ETF regression coverage close to the ETF-owned package instead of mixing it into Alpaca-only test modules.
