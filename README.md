# alpaca-connectors

`alpaca-connectors` connects Alpaca-backed assets, market data, brokerage accounts, holdings,
user-maintained universes, and analytical portfolios to Main Sequence and ms-markets.

## Capabilities

- **Project State:** API health, capability catalog, migrated datasets, account count, and
  universe-source count.
- **Assets:** provider-native Alpaca UUID identity, required Alpaca details, and optional OpenFIGI enrichment.
- **Universe Sources:** durable user-maintained extraction URLs in the project
  `UniverseSource` MetaTable.
- **Universes:** connector-owned `AssetUniverse` registrations linking one explicit source to one
  ms-markets `AssetCategory` materialization target through real foreign keys.
- **Market Data:** maintain reusable bar configurations and query or publish Alpaca OHLCV
  observations in migrated asset-indexed tables.
- **Accounts:** register and maintain Alpaca accounts using Main Sequence Secret names only.
- **Holdings:** capture Alpaca positions and cash as immutable ms-markets account snapshots.
- **Portfolios:** publish scheduled Universe-backed ETF signals and create durable scheduled
  analytical ETF-tracking portfolios from existing signals, persistent interpolated Alpaca bars,
  and reusable persisted-calendar rebalance configurations.

ETF extraction is supplied by `etfhextractor`; it is an input to Universes and Portfolios, not the
project's top-level ontology.

## Repository Boundaries

- `src/assets/`: instrument discovery, FIGI resolution, and strict registration.
- `src/universes/`: source configurations, registered Asset Universes, and category materialization.
- `src/market_data/`: migrated price storage, updater, queries, and account-backed execution.
- `src/account/`: account identity, Secret-name resolution, registration, and refresh.
- `src/holdings/`: Alpaca-position translation and canonical snapshot publication.
- `src/portfolios/`: analytical portfolio construction.
- `src/cli/`: thin command adapters over reusable services.
- `src/jobs/`: reviewed launchers for platform-managed execution.
- `api/`: FastAPI-only contracts, pagination, routing, and response shaping.
- `.mainsequence/workflows/`: backend-validated Job and FastAPI deployment declarations.
- `docs/`: architecture and operating instructions.

The sibling Command Center static application consumes the FastAPI collection and discovery
contracts without moving business logic into the frontend.

## Install And Migrate

Python 3.13 is required. Dependency ranges remain unlocked in `pyproject.toml`; `uv.lock` records
the reproducible resolution.

```bash
uv sync
mainsequence code-repository refresh-token --path .
mainsequence migrations upgrade --provider src.migrations:migration head
alpaca-connectors portfolio prepare-interpolated-prices
alpaca-connectors universe-source seed-defaults
```

The portfolio preparation command derives and migrates the persistent `InterpolatedPrices` tables
from the registered Alpaca bars profiles. The explicit seed command creates the starter IVV source
idempotently. Application startup never creates schema and never seeds rows.

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

Manage extraction targets and registered universes:

```bash
alpaca-connectors universe-source list
alpaca-connectors universe-source create \
  --name "S&P 500 source" \
  --symbol IVV \
  --url "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf"
alpaca-connectors universe-source preview <SOURCE_UID>
alpaca-connectors universe list
alpaca-connectors universe run <UNIVERSE_UID>
alpaca-connectors universe run <UNIVERSE_UID> --execute
```

An executed Universe run extracts once, bulk-registers missing Alpaca assets, bulk-replaces the
linked category membership, and publishes the complete observed weights as one canonical
`SignalWeightsStorage` batch. `universe_uid` defines signal identity; the required `account_uid`
is execution configuration only.

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

Create one dedicated scheduled Job for a Universe-backed signal:

```bash
alpaca-connectors signal create \
  --name "Daily S&P 500 observation" \
  --universe-uid <UNIVERSE_UID> \
  --account-uid <ACCOUNT_UID> \
  --schedule-type interval \
  --schedule-every 1 \
  --schedule-period days
alpaca-connectors signal run <CONFIGURATION_UID>
```

The JobRun receives no signal arguments. Its launcher resolves the stored configuration through
`JOB_RUN_UID` and the owning Job. Each configuration owns one Job. The current CodeRepositoryBranch
resolves Environment scope automatically; the user never supplies an Environment UID.

Create a reusable rebalance policy and one durable Portfolio Configuration with its dedicated Job:

```bash
alpaca-connectors portfolio rebalance create \
  --name "NYSE close" \
  --strategy calendar_event_signal \
  --calendar-identifier NYSE \
  --session-label regular \
  --rebalance-event market_close \
  --rebalance-cadence every_session
alpaca-connectors portfolio create \
  --name "Daily IVV analytical portfolio" \
  --signal-configuration-uid <SIGNAL_CONFIGURATION_UID> \
  --bars-configuration-uid <BARS_CONFIGURATION_UID> \
  --rebalance-configuration-uid <REBALANCE_CONFIGURATION_UID> \
  --valuation-maximum-staleness-seconds 86400 \
  --schedule-type interval \
  --schedule-every 1 \
  --schedule-period days
alpaca-connectors portfolio run <PORTFOLIO_CONFIGURATION_UID>
```

The Portfolio Configuration stores calculation intent only. Schedule, compute, image, and
automatic-deployment state remain on its Job. The JobRun has no business arguments and resolves
the Portfolio Configuration from `JOB_RUN_UID` and the owning Job. Rebalances use actual persisted
market events and the latest signal observed at or before each event; signal observations still do
not claim exact point-in-time ETF holdings or live execution.

Exact asset registration remains independently available:

```bash
alpaca-connectors asset register --account-uid <ACCOUNT_UID> --symbols NVDA,AAPL
```

Provider-derived constituent registration belongs to a configured Asset Universe. A Universe is
the durable ETF holdings source used to resolve current constituents and weights. Run receives an
Alpaca account as serialized signal-updater runtime configuration so scheduled execution can
resolve its Secret names; the account is not stored on the Universe and does not change the final
signal UID.

## API

```bash
uv run uvicorn api.app.main:app --reload
```

For joint local debugging, launch **Debug Alpaca API + Command Center Site** from VS Code. It starts
FastAPI on port `8321`, the sibling Vite site on port `5421`, and opens Chrome with JavaScript
debugging enabled. See [the development workflow](docs/operations/development.md).

The API exposes `/v1/accounts`, account holdings, `/v1/assets`, `/v1/universe-sources`,
`/v1/universes`, `/v1/market-data/bar-configurations`, `/v1/market-data/datasets`,
`/v1/signal-jobs`, `/v1/portfolio-configurations`,
`/v1/portfolio-rebalance-configurations`, `/v1/operations/job-runs/{job_run_uid}`, and
`/v1/project-state`. Collections use
authoritative `pageInfo`; selectable collections have separate resource-discovery endpoints.
Main Sequence injects the authenticated request identity in deployed FastAPI requests.

See [the documentation map](docs/SUMMARY.md) and [API reference](docs/api.md).
