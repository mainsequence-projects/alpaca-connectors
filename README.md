# alpaca-connectors

`alpaca-connectors` connects Alpaca-backed assets, market data, brokerage accounts, holdings,
user-maintained universes, and analytical portfolios to Main Sequence and ms-markets.

## Capabilities

- **Project State:** API health, capability catalog, migrated datasets, account count, and
  universe-source count.
- **Assets:** strict Alpaca and OpenFIGI resolution and Main Sequence public-asset registration.
- **Universe Sources:** durable user-maintained extraction URLs in the project
  `UniverseSource` MetaTable.
- **Universes:** provider-derived holdings materialized as managed ms-markets `AssetCategory`
  memberships.
- **Market Data:** maintain reusable bar configurations and query or publish Alpaca OHLCV
  observations in migrated asset-indexed tables.
- **Accounts:** register and maintain Alpaca accounts using Main Sequence Secret names only.
- **Holdings:** capture Alpaca positions and cash as immutable ms-markets account snapshots.
- **Portfolios:** build analytical ETF-tracking portfolios from extracted weights and Alpaca bars.

ETF extraction is supplied by `etfhextractor`; it is an input to Universes and Portfolios, not the
project's top-level ontology. The Streamlit work is intentionally unchanged and is not part of this
backend refactor.

## Repository Boundaries

- `src/assets/`: instrument discovery, FIGI resolution, and strict registration.
- `src/universes/`: durable source rows, extraction preview, and category materialization.
- `src/market_data/`: migrated price storage, updater, queries, and account-backed execution.
- `src/account/`: account identity, Secret-name resolution, registration, and refresh.
- `src/holdings/`: Alpaca-position translation and canonical snapshot publication.
- `src/portfolios/`: analytical portfolio construction.
- `src/cli/`: thin command adapters over reusable services.
- `src/jobs/`: reviewed launchers for platform-managed execution.
- `api/`: FastAPI-only contracts, pagination, routing, and response shaping.
- `.mainsequence/workflows/`: backend-validated Job and FastAPI deployment declarations.
- `docs/`: architecture and operating instructions.

The repository currently provides backend surfaces only. A future static application can consume
the FastAPI collection and discovery contracts without moving business logic into a frontend.

## Install And Migrate

Python 3.13 is required. Dependency ranges remain unlocked in `pyproject.toml`; `uv.lock` records
the reproducible resolution.

```bash
uv sync
mainsequence code-repository refresh-token --path .
mainsequence migrations upgrade --provider src.migrations:migration head
alpaca-connectors universe-source seed-defaults
```

The explicit seed command creates the starter IVV source idempotently. Application startup never
creates schema and never seeds rows.

## Core CLI

Account registration accepts names of existing Main Sequence Secrets, never key values:

```bash
alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper \
  --plan-only

alpaca-connectors account list
alpaca-connectors account refresh <ACCOUNT_UID>
alpaca-connectors holdings capture --account-uid <ACCOUNT_UID> --execute
```

Manage extraction targets and materialized universes:

```bash
alpaca-connectors universe-source list
alpaca-connectors universe-source create \
  --name "S&P 500 source" \
  --symbol IVV \
  --url "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf"
alpaca-connectors universe-source preview <SOURCE_UID>
alpaca-connectors universe sync --source-uid <SOURCE_UID> --execute
alpaca-connectors universe list
```

Create a reusable update configuration and run it:

```bash
alpaca-connectors market-data dataset list
alpaca-connectors market-data bar-configuration create \
  --name "Daily account holdings" \
  --account-uid <ACCOUNT_UID> \
  --asset-source account_holdings \
  --frequency 1d \
  --feed sip \
  --adjustment all
alpaca-connectors market-data update \
  --configuration-uid <CONFIGURATION_UID> \
  --execute
alpaca-connectors market-data prices \
  --dataset-uid <META_TABLE_UID> \
  --asset-uids <ASSET_UID>
```

The FastAPI update action queues the generic `Alpaca Bars Update` Job with only the stored
configuration UID. Its JobRun can be polled at `/v1/operations/job-runs/{job_run_uid}`; the API
does not execute the bars update in its own process.

Exact asset registration remains independently available:

```bash
alpaca-connectors asset register --symbols NVDA,AAPL
alpaca-connectors asset register --seed-tickers IVV --component-provider ishares --execute
```

## API

```bash
uv run uvicorn api.app.main:app --reload
```

For joint local debugging, launch **Debug Alpaca API + Command Center Site** from VS Code. It starts
FastAPI on port `8321`, the sibling Vite site on port `5421`, and opens Chrome with JavaScript
debugging enabled. See [the development workflow](docs/operations/development.md).

The API exposes `/v1/accounts`, account holdings, `/v1/assets`, `/v1/universe-sources`,
`/v1/universes`, `/v1/market-data/bar-configurations`, `/v1/market-data/datasets`,
`/v1/operations/job-runs/{job_run_uid}`, and `/v1/project-state`. Collections use
authoritative `pageInfo`; selectable collections have separate resource-discovery endpoints.
Main Sequence injects the authenticated request identity in deployed FastAPI requests.

See [the documentation map](docs/SUMMARY.md) and [API reference](docs/api.md).
