# Asset Registration Flow

## Goal

Register Alpaca US equity assets as **ms-markets** assets using Alpaca's immutable asset UUID.
OpenFIGI metadata is optional enrichment and never an identity prerequisite.

Main module:

- `src/assets/alpaca_us_equities.py`

Primary CLI:

- `src/cli/`

## Asset model (ms-markets)

Assets are written through the typed ms-markets API (`msm.api.assets`), not the old
`mainsequence.client`:

- `Asset.uid` is the internal Main Sequence UUID
- `Asset.unique_identifier` is `ALPACA::<alpaca_asset_uuid>`
- the required project-owned `AlpacaAssetDetails` row is keyed by `asset_uid` and stores the
  Alpaca UUID, symbol, exchange, status, capabilities, raw payload, and refresh time
- `OpenFigiDetails` is an optional one-to-one enrichment row; it is never used as the canonical
  identity or as a registration gate
- registration bulk-upserts the `AssetType`, `Asset`, and `AlpacaAssetDetails` rows, then
  bulk-upserts `OpenFigiDetails` only when enrichment succeeds
- all display snapshots for one registration request are published through one batched
  `AssetSnapshot` update rather than one TimeIndexTableUpdater run per asset
- the runtime must be attached first via `src.runtime.start_markets_engine()` (the CLI does this)

## Registration Contract

A symbol is eligible for registration when:

1. Alpaca resolves it as a US equity in one all-status bulk catalog request
2. Alpaca provides a valid immutable asset UUID
3. it is missing from Main Sequence when checked by `ALPACA::<uuid>`

Missing or failed OpenFIGI enrichment is returned as a warning. The asset remains eligible.
Alpaca `status` and `tradable` values are stored on `AlpacaAssetDetails`; inactive and
non-tradable values do not make an asset ineligible for registration.

## Application progress

The static application uses the observable API operation contract rather than waiting on a
single long-running response. The API persists these real orchestration boundaries:

1. prepare required exact-symbol input;
2. resolve the selected registered Alpaca account and its stored Secret references;
3. resolve all requested US-equity identities from one all-status catalog request;
4. validate immutable Alpaca asset UUIDs;
5. attempt optional OpenFIGI enrichment;
6. check canonical Alpaca identifiers against registered Main Sequence assets;
7. register missing assets or refresh required Alpaca details for an execution request;
8. finalize the plan or execution result.

Each step exposes `pending`, `running`, `succeeded`, `failed`, or `skipped`, with timestamps and a
safe user-facing message. Alpaca identity, Main Sequence lookup, and registration failures remain
terminal. OpenFIGI timeout, HTTP, DNS, invalid-response, and unmatched cases are returned in
`warnings_by_symbol`; the operation still succeeds. See
[FastAPI Backend](../api.md#observable-asset-registration) for the start and polling routes.

## Stock vs ETP Classification

Optional enrichment classifies Alpaca symbols through ordered FIGI passes:

1. `Common Stock`
2. `ETP`
3. `REIT`

The FIGI market-sector and security-type constants are local string literals in `src/settings.py`
(`"Equity"`, `"Common Stock"`, `"ETP"`, `"REIT"`). They previously came from
`mainsequence.client.MARKETS_CONSTANTS`, which was removed in SDK 4.x.

## Provider credentials

Asset discovery and registration require a registered Alpaca account UID. The workflow reads the
two Main Sequence Secret names stored on that account's `AlpacaAccountDetails` row and resolves
their values only at the provider boundary. The public CLI and API never accept credential values,
and there is no process-environment or conventional-name fallback.

## Symbol Normalization

The registration path normalizes common class-share aliases when resolving against Alpaca, for example:

- `BRKB` -> `BRK.B`
- `BFB` -> `BF.B`

Slash and dash variants are also considered.

## CLI Input

```bash
alpaca-connectors asset register --account-uid <ACCOUNT_UID> --symbols AAPL,MSFT,NVDA
```

The Assets command never expands ETF or provider seeds. A configured `AssetUniverse` owns source
extraction and invokes the same reusable registration service with its explicit extracted symbols
and the account selected for that Run before refreshing membership. The Universe itself remains
account-independent.

## Output Reporting

The script reports:

- symbols missing from Alpaca
- symbols without optional OpenFIGI enrichment and their warnings
- symbols already registered
- symbols that would be created on `--execute`
