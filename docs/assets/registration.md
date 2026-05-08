# Asset Registration Flow

## Goal

Register Alpaca US equity assets in MainSequence strictly through FIGI resolution.

Main module:

- `src/assets/alpaca_us_equities.py`

Primary CLI:

- `src/cli/`

## Registration Contract

A symbol is eligible for registration only if:

1. it exists in the Alpaca US equity universe
2. it resolves to a FIGI
3. it is missing from MainSequence when checked by FIGI

If FIGI is missing, the symbol is reported and skipped. There is no fallback custom-asset registration path.

## Stock vs ETP Classification

The registration plan classifies Alpaca symbols through ordered FIGI passes:

1. `Common Stock`
2. `ETP`
3. `REIT`

The FIGI market-sector and security-type constants are loaded from MainSequence constants through `src/settings.py`.

## Environment And Secrets

Alpaca credentials are resolved in this order:

1. `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` from the process environment
2. MainSequence secrets with the same names

If neither source exists, the code fails fast.

## Symbol Normalization

The registration path normalizes common class-share aliases when resolving against Alpaca and FIGI, for example:

- `BRKB` -> `BRK.B`
- `BFB` -> `BF.B`

Slash and dash variants are also considered.

## CLI Modes

### Exact symbols only

```bash
alpaca-connectors asset register --symbols AAPL,MSFT,NVDA
```

### ETF component expansion

```bash
alpaca-connectors asset register --seed-tickers IVV --component-provider ishares
```

This remains an orchestration flow. ETF expansion is owned by `etf_extraction/`, and the
resulting explicit symbols are then passed into the Alpaca registration service in `src/assets/`.

## Strict Extraction Rule

ETF component extraction is only allowed when both are explicitly provided:

- `--seed-tickers`
- `--component-provider`

Otherwise the script does not expand holdings.

## Output Reporting

The script reports:

- symbols missing from Alpaca
- symbols missing FIGI
- symbols already registered
- symbols that would be created on `--execute`
