# Project Brief

## Current Goal

Keep `AGENTS.md` aligned with the current Alpaca connector workflows so future agents use the
project CLI and the existing reusable `src/` implementation instead of reviving legacy scripts.

## Success Condition

- `AGENTS.md` project-specific instructions describe the supported Alpaca CLI workflows.
- Asset registration is documented as `alpaca-connectors asset register`.
- Holdings category creation is documented as `alpaca-connectors holdings-category create`.
- Daily stock-bar updates are documented as `alpaca-connectors bars run` or
  `alpaca-connectors asset <ticker> update_prices <period>`.
- The instructions tell future agents to reuse the existing `src/` modules and not recreate the
  removed legacy scripts.

## Scope

In scope:

- `AGENTS.md` project-specific instructions.
- Documentation of the supported CLI entry points and their expected usage.
- Documentation of the repository-specific workflow boundaries for Alpaca registration,
  holdings-category sync, and stock-bar updates.

Out of scope unless requested:

- Changing CLI behavior.
- Live platform execution of registration, holdings-category sync, or daily stock-bar updates.
- SDK upgrades beyond documenting the currently detected version gap.
