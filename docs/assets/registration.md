# Asset Registration Flow

## Goal

Register Alpaca US equity assets as **ms-markets** assets strictly through FIGI resolution.

Main module:

- `src/assets/alpaca_us_equities.py`

Primary CLI:

- `src/cli/`

## Asset model (ms-markets)

Assets are written through the typed ms-markets API (`msm.api.assets`), not the old
`mainsequence.client`:

- the canonical `Asset.unique_identifier` for an equity is its **FIGI**; identity is a UUID
  (`Asset.uid`), not the old integer `id`
- provider facts (ticker, name, exchange, security type) are stored on `OpenFigiDetails` keyed by
  `asset_uid` — they are **not** columns on the asset row
- registration upserts the `Asset` row + its `OpenFigiDetails` (built directly from the OpenFIGI
  classification, no re-query); it replaces the old `Asset.register_asset_from_figi(...)`
- the runtime must be attached first via `src.runtime.start_markets_engine()` (the CLI does this)

## Registration Contract

A symbol is eligible for registration only if:

1. it exists in the Alpaca US equity universe
2. it resolves to a FIGI
3. it is missing from MainSequence when checked by FIGI

If FIGI is missing, the symbol is reported and skipped. There is no fallback custom-asset registration path.

## Application progress

The Command Center application uses the observable API operation contract rather than waiting on a
single long-running response. The API persists these real orchestration boundaries:

1. prepare exact-symbol input or expand ETF seeds;
2. retrieve Alpaca credentials and load the active US-equity catalog;
3. resolve the matching Alpaca symbols through OpenFIGI;
4. check resolved FIGIs against registered Main Sequence assets;
5. register missing assets for an execution request;
6. finalize the plan or execution result.

Each step exposes `pending`, `running`, `succeeded`, `failed`, or `skipped`, with timestamps and a
safe user-facing message. Errors identify the failed dependency and distinguish credential lookup,
Alpaca catalog loading, OpenFIGI timeout, OpenFIGI HTTP/DNS/response failures, Main Sequence asset
lookup, and registration failure. If an upstream Alpaca or credential step fails, the message says
that OpenFIGI was not called. See [FastAPI Backend](../api.md#observable-asset-registration) for
the start and polling routes.

## Stock vs ETP Classification

The registration plan classifies Alpaca symbols through ordered FIGI passes:

1. `Common Stock`
2. `ETP`
3. `REIT`

The FIGI market-sector and security-type constants are local string literals in `src/settings.py`
(`"Equity"`, `"Common Stock"`, `"ETP"`, `"REIT"`). They previously came from
`mainsequence.client.MARKETS_CONSTANTS`, which was removed in SDK 4.x.

## Provider credentials

Asset discovery/registration is a repository bootstrap flow and resolves the conventional
provider credentials in this order:

1. `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` from the process environment
2. MainSequence secrets with the same names

If neither source exists, the code fails fast.

This is distinct from account and market-data operations. Those select a registered account and
resolve the two Main Sequence Secret names stored on its `AlpacaAccountDetails` row; their public
CLI/API never accepts credential values.

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

This remains an orchestration flow. ETF expansion is delegated to the external `etfhextractor`
dependency through `src.universes`, and the resulting explicit symbols are then passed into
the Alpaca registration service in `src/assets/`.

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
