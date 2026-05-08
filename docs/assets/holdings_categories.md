# Holdings Categories

See also:

- `docs/etf_extraction.md` for the ETF-owned architecture, provider flow, and separation from Alpaca logic

## Goal

Create a MainSequence `AssetCategory` from an ETF's published holdings.

Main module:

- `etf_extraction/holdings_categories.py`

Primary CLI:

- `src/cli/`

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
