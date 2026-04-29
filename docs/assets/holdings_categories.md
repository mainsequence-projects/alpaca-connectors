# Holdings Categories

## Goal

Create a MainSequence `AssetCategory` from an ETF's published holdings.

Main module:

- `src/holdings_categories.py`

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
3. check that extracted components exist in Alpaca
4. check that extracted components resolve to FIGI
5. check that those FIGI-resolved assets are already registered in MainSequence
6. create or refresh the `AssetCategory`

## Blockers

Category creation is refused when any extracted holding:

- is not available in Alpaca
- does not resolve to FIGI
- is not already registered in MainSequence

## Provider Inference

The ETF-to-provider mapping is stored in:

- `data/seed_universes.yaml`

## Example

Dry run:

```bash
alpaca-connectors holdings-category create --etf-ticker IVV
```

Execute:

```bash
alpaca-connectors holdings-category create --etf-ticker IVV --execute
```
