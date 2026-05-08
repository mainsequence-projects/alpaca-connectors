# Handoff: ETF Registration Logic and Alpaca Overlap

Project root:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153`

This document focuses only on the ETF registration and ETF holdings expansion logic. The goal is to make clear which parts belong to ETF/provider extraction, which parts belong to Alpaca interaction, and where the two are currently mixed.

## Current ETF Registration Flow

The ETF registration flow starts when an operator uses:

```bash
alpaca-connectors asset register --seed-tickers IVV --component-provider ishares
```

The flow is:

1. CLI receives an ETF seed ticker.
2. CLI requires or receives a component provider.
3. ETF provider extractor downloads/parses ETF holdings.
4. Extracted holdings become candidate component symbols.
5. Candidate symbols are checked against Alpaca's active US equity universe.
6. Alpaca-matched symbols are sent through OpenFIGI classification.
7. Main Sequence asset registration is planned or executed.

The important overlap is step 5: ETF-derived symbols enter Alpaca-specific validation and registration. That is the boundary to separate if this project should become purely Alpaca-interaction related.

## Operator Entry Point

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/cli/asset.py`

Relevant behavior:

`configure_register_parser` exposes `alpaca-connectors asset register`.

The ETF-specific path is triggered by:

```bash
--seed-tickers <ETF>
--component-provider <provider>
```

The Alpaca-only path is triggered by:

```bash
--symbols <AAPL,MSFT,...>
```

Current overlap:

`asset.py` accepts both direct Alpaca symbols and ETF seed tickers. That means the Alpaca registration command currently owns an ETF expansion mode.

Separation recommendation:

Keep both operator paths, but route them through independent concerns:

- direct symbol path -> Alpaca registration service
- ETF seed path -> ETF expansion service -> Alpaca registration service

The CLI can continue exposing `--seed-tickers` and `--component-provider`, but it should orchestrate independent services instead of making the Alpaca registration planner own ETF expansion.

## Core Alpaca Registration Function

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/assets/alpaca_us_equities.py`

Key function:

`build_alpaca_us_equity_registration_plan(...)`

What it does:

1. Validates that the caller used either `symbols` or `seed_tickers`, not both.
2. If `symbols` is provided, it works as a pure Alpaca registration planner.
3. If `seed_tickers` is provided, it calls the ETF extractor registry.
4. Fetches Alpaca active US equities.
5. Resolves requested/extracted symbols against Alpaca symbols.
6. Queries OpenFIGI to classify symbols.
7. Builds an `AlpacaEquityRegistrationPlan`.

Current overlap:

This file is fundamentally Alpaca registration logic, but it imports ETF extraction types and registry helpers:

```python
from src.extractors import ExpandedSymbolUniverse, build_component_extractor
```

It also stores ETF expansion state directly inside the Alpaca registration model:

```python
seed_symbol_universe: ExpandedSymbolUniverse | None = None
component_provider: str | None = None
```

Separation recommendation:

The Alpaca registration planner should accept only final symbols. ETF expansion should happen before calling it. A cleaner boundary would be:

```python
build_alpaca_us_equity_registration_plan(symbols=[...])
```

An external ETF module would produce that symbol list.

## ETF Extractor Registry

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/registry.py`

Key function:

`build_component_extractor(provider, ...)`

What it does:

Maps provider names to provider-specific ETF holdings extractors:

- `ishares`
- `invesco`
- `vanguard`
- `state_street`

Current overlap with Alpaca:

This module itself is not Alpaca-specific. It only knows how to instantiate ETF holdings extractors. The overlap happens because `alpaca_us_equities.py` calls this registry directly during Alpaca registration planning.

Separation recommendation:

This registry should move with ETF logic. The Alpaca connector should not import it.

## Shared ETF Extraction Types and Parsers

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/common.py`

Key objects:

`ExpandedSymbolUniverse`

`HoldingsExtractor`

`parse_tabular_tickers`

`parse_html_table_tickers`

`parse_excel_xml_worksheet_rows`

`parse_xlsx_rows`

What it does:

This is the generic ETF holdings parsing layer. It normalizes tickers, parses provider files/pages, and defines how a seed ETF becomes an expanded universe:

```python
ETF seed ticker -> component tickers -> expanded symbol universe
```

Current overlap with Alpaca:

`ExpandedSymbolUniverse` is imported into Alpaca registration models. That creates a direct type dependency from Alpaca logic to ETF expansion logic.

Separation recommendation:

Move this entire file with ETF logic. Alpaca code should receive plain symbols and report Alpaca-specific missing symbols separately.

## Provider-Specific ETF Extractors

Absolute paths:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/ishares.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/invesco.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/vanguard.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/state_street.py`

What they do:

These files fetch and parse holdings from ETF provider websites. They are not Alpaca-specific.

iShares:

Downloads/parses iShares workbook-style holdings and filters for equity rows.

Invesco:

Resolves Invesco holdings APIs from pages, uses browser fallback when needed, and parses JSON or table data.

Vanguard:

Calls Vanguard portfolio holdings JSON endpoint and extracts tickers.

State Street:

Resolves product metadata, finds daily holdings xlsx files, and parses workbook rows.

Current overlap with Alpaca:

The provider files have no natural Alpaca dependency. They are only pulled into Alpaca registration because `build_alpaca_us_equity_registration_plan` supports `seed_tickers`.

Separation recommendation:

Move all provider extractors out of the Alpaca connector if ETF holdings extraction should be separate. Keep only the final Alpaca registration call in this repo.

## Browser Fallback for ETF Providers

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/browser.py`

What it does:

Provides Playwright helpers used by ETF providers, especially Invesco, when static HTTP requests cannot reliably expose holdings data.

Current overlap with Alpaca:

No natural Alpaca dependency. It exists only for ETF provider extraction.

Separation recommendation:

This should move with ETF provider logic. A pure Alpaca connector should not require Playwright for ETF website parsing.

## ETF Provider Configuration

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/settings.py`

ETF-specific items:

`IsharesHoldingsSource`

`InvescoHoldingsSource`

`VanguardHoldingsSource`

`StateStreetHoldingsSource`

`ISHARES_PRODUCT_LISTING_URL`

`INVESCO_HOLDINGS_LANDING_URL_TEMPLATE`

`VANGUARD_PROFILE_URL_TEMPLATE`

`STATE_STREET_QUICK_INFO_URL_TEMPLATE`

`SUPPORTED_COMPONENT_PROVIDERS`

`ETF_PROVIDER_MAP_NORMALIZED`

What it does:

This file currently mixes Alpaca credentials/settings with ETF provider settings.

Current overlap:

Alpaca settings and ETF provider settings live in one module. That makes pure Alpaca code import configuration related to iShares, Invesco, Vanguard, and State Street.

Separation recommendation:

Keep Alpaca settings here:

```python
ALPACA_API_KEY_SECRET_NAME
ALPACA_SECRET_KEY_SECRET_NAME
get_alpaca_api_key
get_alpaca_secret_key
```

Move ETF provider URL templates, provider source dataclasses, and provider map loading to an ETF-specific settings module.

## ETF Seed Universe Data

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/data/seed_universes.yaml`

Relevant ETF fields:

`etfs_main_tickers`

`etf_provider_map_normalized`

What it does:

This file maps ETF tickers to holdings providers and defines seed ETF universes.

Current overlap with Alpaca:

The Alpaca connector reads this file only because it supports ETF expansion during asset registration.

Separation recommendation:

Move ETF seed universe data out of the Alpaca connector. A pure Alpaca connector should not need an ETF provider map. It should only receive concrete symbols to validate/register.

Important caveat:

The map currently contains:

```yaml
USO: uscf
```

But `uscf` is not included in `SUPPORTED_COMPONENT_PROVIDERS`. This is an ETF extraction concern, not an Alpaca concern.

## Holdings Category Logic

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/holdings_categories.py`

What it does:

Builds and syncs Main Sequence `AssetCategory` objects from ETF holdings. It creates identifiers like:

```text
HOLDINGS__IVV
```

It also:

1. Infers ETF provider.
2. Expands ETF holdings.
3. Checks whether components are already registered as Main Sequence assets.
4. Detects ambiguous registered ticker mappings in Main Sequence.
5. Writes/refeshes the `AssetCategory`.

Current overlap:

This file is ETF-owned. Alpaca validation belongs later, when Alpaca consumers read from the category.

Separation recommendation:

If keeping the Alpaca connector pure, this file should move out or be split:

- ETF side: provider inference and holdings extraction.
- Alpaca side: validate/register final symbols.
- Main Sequence side: sync category from already-registered asset ids.

## Holdings Category CLI

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/cli/holdings_category.py`

Command:

```bash
alpaca-connectors holdings-category create --etf-ticker IVV
```

What it does:

CLI wrapper around `build_holdings_asset_category_plan` and `sync_holdings_asset_category`.

Current overlap:

The command exposes ETF holdings category creation through the Alpaca connector CLI.

Separation recommendation:

Keep the command if it is operationally useful, but split its internals into independent concerns:

- ETF provider inference and component extraction
- Alpaca symbol validation and asset registration lookup
- Main Sequence `AssetCategory` sync

The command should orchestrate these concerns without embedding all logic in one category planner.

## ETF Maintenance Routine Job

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/jobs/run_etf_maintenance_routines.py`

Config path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/data/etf_maintenance_routines.yaml`

What it does:

Runs multi-step ETF routines:

1. Register ETF holdings assets.
2. Sync holdings category.
3. Update ETF price.
4. Update holdings category prices.

Current overlap:

This is the largest operational overlap. It chains ETF extraction, Alpaca registration, Main Sequence category sync, and Alpaca bars updates.

Separation recommendation:

For a pure Alpaca connector, keep only price update jobs that operate on explicit Alpaca assets or explicit Main Sequence `AssetCategory` identifiers. Move ETF maintenance orchestration out.

## Fixed IVV Holdings Bars Job

Absolute path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/jobs/run_daily_stock_bars_holdings_ivv.py`

Schedule path:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/scheduled_jobs.yaml`

What it does:

Runs bars update for the existing category:

```text
HOLDINGS__IVV
```

Current overlap:

This job does not extract ETF holdings at runtime. It only assumes a holdings-based category already exists.

Separation recommendation:

This can remain in a pure Alpaca connector only if the category is treated as an external input. The job should not own creation or refresh of `HOLDINGS__IVV`.

## Clean Separation Target

To make this repository purely Alpaca-interaction related, the Alpaca connector should own:

1. Fetch Alpaca active assets.
2. Resolve explicit symbols against Alpaca.
3. Query OpenFIGI for Alpaca-resolved symbols.
4. Register Main Sequence assets from Alpaca symbols.
5. Update Alpaca stock bars for explicit assets or pre-existing categories.

ETF/universe logic should own:

1. ETF provider mapping.
2. Provider-specific holdings downloads.
3. Browser fallback for provider pages.
4. Seed ETF universe files.
5. ETF holdings expansion.
6. Holdings-based category construction policy.

## Files To Move or Split First

Strong ETF-only candidates:

Target root for all ETF-owned code and configuration:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/`

Current ETF-only candidates to place under that root:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/data/seed_universes.yaml`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/data/etf_maintenance_routines.yaml`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/jobs/run_etf_maintenance_routines.py`

Mixed candidates that need careful splitting:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/assets/alpaca_us_equities.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/settings.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/holdings_categories.py` compatibility shim

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/cli/asset.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/cli/holdings_category.py`

Likely Alpaca-only files to preserve:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/data_nodes/alpaca_bars.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/cli/bars.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/jobs/run_daily_stock_bars_holdings_ivv.py` if the category is external/pre-existing.

## Separation Tasks

Use this checklist to separate ETF extraction/registration logic from Alpaca interaction logic without deleting functionality. The target is independent concerns with explicit interfaces, not feature removal.

### [x] Task 1: Define the Final Boundary

Scope:

Decide that this repository owns only Alpaca interaction and does not own ETF holdings extraction.

Expected outcome:

The project supports both direct Alpaca symbols and ETF seed tickers, but the Alpaca registration service itself accepts only explicit symbols. ETF seed tickers are resolved by a separate ETF expansion service before Alpaca registration is called.

Acceptance criteria:

- `alpaca-connectors asset register --symbols AAPL,MSFT` remains supported.
- `alpaca-connectors asset register --seed-tickers IVV --component-provider ishares` remains supported.
- ETF provider selection happens outside the Alpaca registration service.
- The CLI orchestration is allowed to call both ETF expansion and Alpaca registration.

### [x] Task 2: Split ETF Expansion Out of Alpaca Registration

Primary file:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/assets/alpaca_us_equities.py`

Scope:

Extract ETF expansion out of `build_alpaca_us_equity_registration_plan`.

Current coupling to isolate:

```python
from src.extractors import ExpandedSymbolUniverse, build_component_extractor
```

Current parameters to move into an ETF expansion/orchestration layer under `/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/`:

```python
seed_tickers
component_provider
```

Expected outcome:

`build_alpaca_us_equity_registration_plan` plans registration for explicit Alpaca symbols. A separate orchestrator may still accept ETF seeds, expand them, and then call this function with the resulting symbols.

Acceptance criteria:

- Function accepts `symbols` as the Alpaca registration input.
- Function does not import `src.extractors`.
- ETF expansion output is represented before the Alpaca registration call.
- Missing symbols from Alpaca are reported by the Alpaca registration service.
- ETF unsupported seed/provider issues are reported by the ETF expansion service.

### [x] Task 3: Simplify Asset Registration CLI

Primary file:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/cli/asset.py`

Scope:

Keep ETF-specific CLI arguments, but route them through orchestration instead of direct Alpaca planner coupling.

Arguments to keep as orchestration inputs:

```bash
--seed-tickers
--component-provider
```

Expected outcome:

The CLI still supports both direct symbols and seed tickers, but internally calls the right concern-specific services.

Acceptance criteria:

- `alpaca-connectors asset register --symbols AAPL,MSFT` still works.
- `alpaca-connectors asset register --seed-tickers IVV --component-provider ishares` still works.
- CLI help makes clear that seed tickers use an ETF expansion step before Alpaca registration.
- Tests cover both direct-symbol registration and ETF-seed orchestration.

### [x] Task 4: Isolate Extractor Package Behind a Stable Interface

Current primary directory:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/`

Target directory:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/extractors/`

Scope:

Keep ETF provider extraction functional, but isolate it behind a stable ETF expansion interface inside the root `etf_extraction/` concern folder.

Files:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/common.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/registry.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/ishares.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/invesco.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/vanguard.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/state_street.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/extractors/browser.py`

Target layout:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/extractors/common.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/extractors/registry.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/extractors/ishares.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/extractors/invesco.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/extractors/vanguard.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/extractors/state_street.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/extractors/browser.py`

Expected outcome:

Alpaca registration code no longer depends on provider website scraping/parsing or Playwright browser helpers. ETF orchestration code may still depend on them.

Acceptance criteria:

- No Alpaca registration module imports `src.extractors`.
- ETF orchestration module imports from `etf_extraction`.
- Direct Alpaca-only workflows do not require Playwright.
- ETF-seed workflows may require provider/browser dependencies.

### [x] Task 5: Split Settings

Primary file:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/settings.py`

Scope:

Keep Alpaca credentials and OpenFIGI settings in an Alpaca settings module. Move ETF provider settings into an ETF settings module under `/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/`.

Keep:

```python
ALPACA_API_KEY_SECRET_NAME
ALPACA_SECRET_KEY_SECRET_NAME
get_alpaca_api_key
get_alpaca_secret_key
OPENFIGI_*
get_openfigi_api_key
```

Move into ETF settings:

```python
IsharesHoldingsSource
InvescoHoldingsSource
VanguardHoldingsSource
StateStreetHoldingsSource
ISHARES_PRODUCT_LISTING_URL
INVESCO_HOLDINGS_LANDING_URL_TEMPLATE
VANGUARD_PROFILE_URL_TEMPLATE
STATE_STREET_QUICK_INFO_URL_TEMPLATE
SUPPORTED_COMPONENT_PROVIDERS
ETF_PROVIDER_MAP_NORMALIZED
```

Target ETF settings file:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/settings.py`

Expected outcome:

Settings are grouped by concern:

- Alpaca/OpenFIGI/Main Sequence settings for registration and bars.
- ETF provider settings for holdings expansion.

Acceptance criteria:

- Direct Alpaca workflows do not load `data/seed_universes.yaml`.
- ETF workflows load ETF provider maps through ETF settings.
- Provider URL templates are absent from Alpaca-only settings.
- `USO: uscf` mismatch is handled as an ETF provider configuration issue.

### [x] Task 6: Split Holdings Category Creation Into Three Services

Primary files:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/holdings_categories.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/cli/holdings_category.py`

Target ETF orchestration files:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/holdings_categories.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/orchestration.py`

Scope:

Separate ETF holdings extraction from Main Sequence `AssetCategory` sync and from later Alpaca consumer validation.

Expected outcome:

Holdings category creation remains available, but internally becomes orchestration across independent services.

Acceptance criteria:

- ETF expansion service resolves ETF seed to component symbols.
- Alpaca registration/lookup service resolves symbols to registered Main Sequence assets.
- Category sync service accepts asset ids and writes the `AssetCategory`.
- Provider inference is not embedded in category sync.

### [x] Task 7: Refactor ETF Maintenance Routine Job Into Orchestration

Primary files:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/jobs/run_etf_maintenance_routines.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/data/etf_maintenance_routines.yaml`

Target ETF routine files:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/jobs/run_etf_maintenance_routines.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/data/etf_maintenance_routines.yaml`

Scope:

Keep the job behavior, but make it explicitly orchestrate independent services.

Expected outcome:

The routine job remains available, but each step delegates to a concern-specific module:

- ETF expansion
- Alpaca registration
- category sync
- Alpaca bars update

Acceptance criteria:

- Scheduled/local job code is orchestration-only.
- Routine config may still reference `etf_ticker` and `component_provider`.
- Each step can be tested independently.
- Price update jobs remain available for explicit categories/assets.

### [x] Task 8: Decide How to Treat `HOLDINGS__IVV`

Primary files:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/src/jobs/run_daily_stock_bars_holdings_ivv.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/scheduled_jobs.yaml`

Scope:

Make clear whether `HOLDINGS__IVV` is produced by ETF orchestration or supplied externally before Alpaca bars updates.

Expected outcome:

The fixed Alpaca bars job treats `HOLDINGS__IVV` as a pre-existing category input. It assumes the category already exists before the bars job runs.

Acceptance criteria:

- The bars job treats `HOLDINGS__IVV` as an input category.
- Documentation states that the fixed bars job assumes the category already exists.
- Documentation may separately describe ETF orchestration that can create or refresh the category, but the bars job itself does not do that.
- Bars update remains independent from category construction.

### [x] Task 9: Update Tests

Primary test files:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/tests/test_alpaca_us_equities.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/tests/test_cli.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/tests/test_etf_components.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/tests/test_holdings_categories.py`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/tests/test_etf_routines.py`

Target ETF test location:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/tests/`

Scope:

Reorganize tests so each concern has its own coverage.

Expected outcome:

Tests separately validate Alpaca behavior, ETF expansion behavior, and orchestration behavior.

Acceptance criteria:

- Direct symbol registration tests remain.
- ETF component extraction tests target the ETF expansion service.
- Holdings category tests target category sync separately from extraction.
- Routine tests verify orchestration and command sequencing.

### [x] Task 10: Update Documentation

Primary docs:

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/README.md`

`/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/docs/`

Scope:

Rewrite docs to describe independent concerns and the orchestration between them.

Expected outcome:

Documentation makes clear that ETF extraction, Alpaca registration, category sync, and bars updates are distinct concerns.

Acceptance criteria:

- README documents direct symbol registration.
- README documents ETF-seed registration as an orchestration flow.
- ETF provider extraction docs describe the ETF expansion concern.
- Operational docs show which module owns each step.

### Suggested Execution Order

- [x] Create the root folder `/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153/etf_extraction/`.
- [x] Create an ETF expansion service interface inside `etf_extraction/`.
- [x] Move ETF extraction modules into `etf_extraction/extractors/`.
- [x] Extract ETF expansion out of `src/assets/alpaca_us_equities.py`.
- [x] Update `src/cli/asset.py` to orchestrate direct symbols and ETF seeds through separate services.
- [x] Split `src/settings.py` into Alpaca/OpenFIGI settings and `etf_extraction/settings.py`.
- [x] Split holdings category creation into ETF expansion, Alpaca lookup, and category sync services.
- [x] Refactor ETF maintenance routines to call the separated services.
- [x] Document whether `HOLDINGS__IVV` is created by orchestration or supplied externally.
- [x] Update tests by concern, with ETF tests under `etf_extraction/tests/`.
- [x] Update documentation.
- [x] Run focused validation for direct Alpaca registration, ETF-seed registration orchestration, category sync, and bars updates.
