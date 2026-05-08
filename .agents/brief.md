# Project Brief

## Current Goal

Keep the Alpaca connector split cleanly between Alpaca-owned logic under `src/` and ETF-owned
logic under `etf_extraction/`, with the ETF extraction package documented as an independent
workflow and dependency boundary.

## Success Condition

- `src/` remains the owner of Alpaca registration, Alpaca bars execution, and shared runtime
  entrypoints.
- `etf_extraction/` remains the owner of ETF provider extraction, ETF settings, and ETF-owned
  holdings category sync.
- The repository documentation contains a standalone ETF extraction page that explains the package
  independently from Alpaca flows.
- Repo docs still describe the supported CLI entry points accurately.

## Scope

In scope:

- Documentation of the ETF extraction package boundary and ownership.
- Documentation of the supported CLI entry points and their expected usage.
- Documentation of the repository-specific workflow boundaries for Alpaca registration,
  ETF extraction, holdings-category sync, and stock-bar updates.

Out of scope unless requested:

- Changing Alpaca or ETF runtime behavior.
- Live platform execution of registration, holdings-category sync, or daily stock-bar updates.
- SDK upgrades beyond documenting the currently detected version gap.
