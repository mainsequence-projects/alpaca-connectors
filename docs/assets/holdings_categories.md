# Holdings Categories

See also:

- `docs/etf_extraction.md` for the ETF-owned architecture, provider flow, and separation from Alpaca logic

## Goal

Create an **ms-markets** `AssetCategory` (`msm.api.assets`) from an ETF's published holdings.

Main module:

- `etf_extraction/holdings_categories.py`

Primary CLI:

- `src/cli/`

## ms-markets specifics

- Category get-or-create uses `AssetCategory.upsert(unique_identifier=, display_name=, description=)`.
- Membership is replaced atomically with `AssetCategory.replace_memberships(category_uid=,
  asset_uids=[...])` (delete-all-then-insert) — replacing the old `remove_assets` + `append_assets`.
- Members are ms-markets asset **uids** (UUIDs), not integer ids.
- A component ticker resolves to an asset via `OpenFigiDetails.ticker` (`src/assets/resolution.py`),
  since the asset row no longer carries a ticker/`current_snapshot`.
- The runtime must be attached first via `src.runtime.start_markets_engine()` (the CLI does this).

## Naming Convention

Holdings categories use:

```text
HOLDINGS__[ETF_TICKER]
```

Example:

```text
HOLDINGS__IVV
```

## Flow

1. infer or receive the ETF holdings provider
2. extract the ETF component tickers
3. check that extracted components are already registered as MainSequence assets
4. detect ambiguous ticker-to-asset matches in MainSequence
5. create or refresh the `AssetCategory`

## Blockers

Category creation is refused when any extracted holding:

- is not already registered in MainSequence
- resolves ambiguously to more than one MainSequence asset

## Provider Inference

The ETF-to-provider mapping is stored in:

- `etf_extraction/data/seed_universes.yaml`

## Example

Dry run:

```bash
alpaca-connectors holdings-category create --etf-ticker IVV
```

Execute:

```bash
alpaca-connectors holdings-category create --etf-ticker IVV --execute
```
