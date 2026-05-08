# ADR: ETF Holdings AssetCategory Ownership

## Status

Accepted

## Context

The project previously created ETF holdings `AssetCategory` objects by passing extracted ETF holdings through Alpaca availability checks, FIGI resolution, and MainSequence asset registration checks before category sync.

That mixed two separate concerns:

- ETF holdings truth:
  what symbols belong to an ETF according to the ETF provider
- Alpaca execution truth:
  what symbols can be consumed by Alpaca registration or Alpaca bars workflows

Those are not the same boundary.

## Decision

ETF holdings categories are owned by `etf_extraction`.

The ETF holdings category flow is:

1. ETF ticker identifies a holdings universe.
2. ETF extraction resolves component symbols from the provider source.
3. ETF category planning looks up those symbols in MainSequence assets.
4. If the symbols are registered uniquely in MainSequence, the ETF-owned `AssetCategory` is synced.

Alpaca is not part of ETF category construction.

Alpaca validation happens later, when Alpaca-specific consumers read assets from the category.

This decision also implies two follow-on rules:

1. ETF provider configuration and seed-universe configuration belong to `etf_extraction`.
2. ETF routine orchestration must call ETF and Alpaca services directly, rather than routing through CLI argument strings.

## Consequences

Positive:

- ETF holdings categories remain faithful to ETF source data.
- Alpaca limitations do not redefine ETF membership.
- The same ETF `AssetCategory` can be reused by non-Alpaca consumers.
- The architecture becomes cleaner:
  `etf_extraction` owns ETF universe construction,
  `src/` owns Alpaca validation and bars execution.

Tradeoffs:

- ETF category sync still requires assets to exist in MainSequence, because `AssetCategory` stores assets, not raw ticker strings.
- A category may be blocked by missing or ambiguous MainSequence assets even when ETF extraction itself succeeded.
- ETF routine execution becomes more explicit in code, which is cleaner architecturally but means orchestration logic now lives in a dedicated service layer instead of being hidden behind CLI indirection.

## Implementation Boundary

`etf_extraction` owns:

- provider inference
- ETF holdings extraction
- ETF provider settings
- seed-universe configuration
- holdings category planning
- holdings category sync
- ETF maintenance orchestration

`src/assets` owns:

- Alpaca symbol lookup
- Alpaca availability checks
- FIGI resolution
- MainSequence asset registration from Alpaca symbols

`src/data_nodes` owns:

- consuming explicit assets or categories for Alpaca bars updates
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
3. ETF holdings-category sync service
4. Alpaca bars execution service

The routine runner should not rebuild CLI command strings and re-enter the application through `main(argv)`.

That pattern hides the true dependencies, makes dry-run output less meaningful, and couples orchestration to presentation-oriented CLI code.

## Settings Boundary

`src/settings.py` is the Alpaca/OpenFIGI/MainSequence runtime settings module.

`etf_extraction/settings.py` is the ETF extraction settings module.

That split means:

- Alpaca registration and Alpaca bars code do not import ETF provider URL patterns.
- ETF extraction code does not need to depend on Alpaca credential/runtime settings.
- the dependency direction is easier to reason about:
  ETF concerns depend on ETF settings,
  Alpaca concerns depend on Alpaca/OpenFIGI settings,
  orchestration composes both.

## Category Consumer Boundary

`HOLDINGS__<ETF>` means:

- the ETF provider says these are the holdings
- MainSequence has assets registered for those holdings

It does not mean:

- Alpaca can price every member
- Alpaca can resolve every member without filtering

Those checks belong to category consumers such as Alpaca bars execution.

## Notes

This decision means `HOLDINGS__<ETF>` represents ETF holdings membership, not "Alpaca-usable ETF holdings membership".
