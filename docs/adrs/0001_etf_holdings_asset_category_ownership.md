# ADR: ETF Holdings AssetCategory Ownership

## Status

Accepted in principle; its registration-order details are superseded by ADR 0005.

## Context

The project previously created ETF holdings `AssetCategory` objects by passing extracted ETF holdings through Alpaca availability checks, FIGI resolution, and MainSequence asset registration checks before category membership refresh.

That mixed two separate concerns:

- ETF holdings truth:
  what symbols belong to an ETF according to the ETF provider
- Alpaca execution truth:
  what symbols can be consumed by Alpaca registration or Alpaca bars workflows

Those are not the same boundary.

## Decision

ETF holdings categories are owned by the ETF holdings extraction layer, now provided by the
external `etfhextractor` dependency.

The ETF holdings category flow is:

1. The operator explicitly registers an `AssetUniverse` with a source URL and symbol.
2. The registration stores required foreign keys to that `UniverseSource` and to one empty
   `AssetCategory`.
3. A separate Run extracts component symbols from exactly the linked source.
4. Run planning resolves those symbols through Alpaca using the account explicitly selected for
   that execution and identifies missing Main Sequence assets as registration work.
5. Execution registers missing Alpaca-backed assets and replaces membership only in the linked
   category after the complete constituent set is available.

This decision also implies two follow-on rules:

1. ETF provider parsing belongs to `etfhextractor`; the connector passes the explicitly configured
   source URL and does not infer a provider or URL.
2. ETF routine orchestration must call ETF and Alpaca services directly, rather than routing through CLI argument strings.

## Consequences

Positive:

- ETF holdings categories remain faithful to ETF source data.
- Alpaca identity resolution is an explicit materialization dependency and does not silently
  filter provider holdings.
- The linked ETF `AssetCategory` can be reused by non-Alpaca consumers.
- The architecture becomes cleaner:
  `etfhextractor` owns ETF universe construction,
  `src/` owns Alpaca validation and bars execution.

Tradeoffs:

- ETF category publication still requires assets to exist in Main Sequence, because
  `AssetCategory` stores assets rather than raw ticker strings; Run now creates missing canonical
  Alpaca-backed assets itself.
- A symbol that Alpaca cannot resolve blocks category publication and is reported explicitly.
- ETF routine execution becomes more explicit in code, which is cleaner architecturally but means orchestration logic now lives in a dedicated service layer instead of being hidden behind CLI indirection.

## Implementation Boundary

`etfhextractor` owns:

- parsing the explicitly selected provider URL
- ETF holdings extraction

`src/universes` owns:

- `UniverseSource` extraction configurations
- `AssetUniverse` identity and its required source/category foreign keys
- Run orchestration across extraction, Alpaca registration, and linked-category membership replacement
- the local adapter around `etfhextractor`

`src/assets` owns:

- Alpaca symbol lookup
- Alpaca availability checks
- FIGI resolution
- MainSequence asset registration from Alpaca symbols

`src/market_data` owns:

- consuming explicit assets or registered Asset Universes for Alpaca bars updates
- resolving whether category members can be used with Alpaca

`src/cli` owns:

- operator-facing argument parsing
- translating CLI input into service calls
- presenting summaries and execution output

It does not own ETF extraction logic or Alpaca registration logic directly.

## Routine Runner Boundary

ETF maintenance routines are orchestration, not business logic.

The routine runner should compose these steps directly:

1. ETF expansion service
2. Alpaca registration service
3. registered Asset Universe Run service
4. Alpaca bars execution service

The routine runner should not rebuild CLI command strings and re-enter the application through `main(argv)`.

That pattern hides the true dependencies, makes dry-run output less meaningful, and couples orchestration to presentation-oriented CLI code.

## Settings Boundary

`src/settings.py` is the Alpaca/OpenFIGI/MainSequence runtime settings module.

`src/universes/etf_holdings.py` is the local adapter over `etfhextractor`.

That split means:

- Alpaca registration and Alpaca bars code do not import ETF provider URL patterns.
- ETF extraction code does not need to depend on Alpaca credential/runtime settings.
- the dependency direction is easier to reason about:
  ETF concerns depend on ETF settings,
  Alpaca concerns depend on Alpaca/OpenFIGI settings,
  orchestration composes both.

## Category Consumer Boundary

The registered Universe UID is not the AssetCategory UID. `AssetUniverse.asset_category_uid` is a
required foreign key to the category that materializes the extracted membership, while
`AssetUniverse.source_uid` is a required foreign key to the exact extraction configuration.
Neither link is stored in `AssetCategory.metadata_json`.

`HOLDINGS__<ETF>` means:

- the ETF provider says these are the holdings
- MainSequence has assets registered for those holdings

It does not mean:

- Alpaca can price every member
- Alpaca can resolve every member without filtering

Those checks belong to category consumers such as Alpaca bars execution.

## Notes

This decision means `HOLDINGS__<ETF>` represents ETF holdings membership, not "Alpaca-usable ETF holdings membership".
