# ADR: Asset Universe Run Owns Constituent Registration

## Status

Accepted. This ADR supersedes the registration-order parts of ADR 0001.

## Context

A provider-derived universe cannot materialize an `AssetCategory` until each extracted symbol has
a canonical Main Sequence Asset. The previous workflow treated missing assets as an operator
blocker and directed the user to copy hundreds of tickers into the standalone Assets workflow.
That split the lifecycle of one configured universe across two unrelated actions and made the
stored universe configuration incomplete.

The standalone Assets workflow is still useful when an operator explicitly wants to register known
symbols. It must not serve as a second, ephemeral ETF-seed configuration surface.

## Decision

`AssetUniverse` stores two explicit relationships:

- `source_uid` selects the exact extraction configuration;
- `asset_category_uid` selects the exact membership materialization target;
The Universe represents the ETF holdings source used to resolve current constituents and weights.
It does not own a brokerage account. `account_uid` is required only in the Run request and selects
the registered Alpaca account whose stored Main Sequence Secret references are used for provider
access and constituent registration. The application never persists or guesses that account.

Universe Run owns this ordered workflow:

1. load the registered universe, source, and category, plus the account selected for this Run;
2. extract the current source holdings through `etfhextractor`;
3. build an Alpaca-native registration plan for the extracted symbols;
4. report symbols missing from Main Sequence as planned registration work;
5. for a current-catalog miss, reuse one exact canonical Alpaca Asset/detail previously registered
   for that symbol through a single set-based lookup; block only when neither identity exists or
   another required dependency fails;
6. on execution, idempotently register or refresh every resolvable Alpaca-backed Asset;
7. replace the linked category membership only after the complete constituent set is available;
8. publish the extracted weights as one `AlpacaETFHoldingsSignal` observation as defined by
   [ADR 0006](0006_universe_backed_alpaca_etf_signal.md).

The standalone `alpaca-connectors asset register` command accepts required exact `--symbols` only.
It has no ETF-seed or component-provider mode.

## Consistency Boundary

The complete registered asset set is the category-membership publication boundary. A failed
provider lookup or asset registration leaves existing membership unchanged. Asset upserts that
completed before a later failure may remain because they are canonical, idempotent resources;
retrying the Run reuses them. Signal storage is a subsequent publication step: if it fails, the Run
is reported as failed rather than claiming that the observation was stored.

## Consequences

- The user configures an ETF or other supported holdings source once, on the universe.
- A list of hundreds of unregistered constituents is no longer presented as manual prerequisite
  work.
- Account credential values never enter the Run request; only a registered Account UID is passed as
  execution context.
- The same Universe can be run with any authorized registered Alpaca account without changing its
  durable identity or source configuration.
- Assets remains a precise explicit-symbol tool and does not duplicate universe configuration.
