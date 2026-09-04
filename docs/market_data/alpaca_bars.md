# Alpaca historical prices

## Goal

Publish and query Alpaca stock OHLCV bars in migration-managed ms-markets asset-indexed
MetaTables. Every update starts from a durable configuration that the user can review before it
runs. The user never selects an output dataset UID for an update.

Main modules:

- `src/market_data/storage.py`: migrated table contracts
- `src/market_data/configurations.py`: stored configuration and explicit-asset membership tables
- `src/market_data/alpaca_bars.py`: `AssetIndexedDataNode` producer
- `src/market_data/services.py`: configuration resolution, dataset catalog, reads, and execution
- `src/cli/bars.py`: thin CLI adapter

## Data contract

The grain is `(time_index, asset_identifier)`. `asset_identifier` is the canonical ms-markets
`Asset.unique_identifier`, with a foreign key to `AssetTable.unique_identifier`. Observations
contain `open`, `high`, `low`, `close`, `volume`, `trade_count`, and `vwap`.

The currently migrated profiles are:

- `1d/iex/raw`
- `1d/sip/all`

Frequency, feed, and adjustment are stored on the configuration. Their triple resolves exactly one
output storage class. A request cannot create a new combination dynamically; adding one requires a
storage class and an Alembic migration.

For daily bars, `time_index` is normalized to 16:00 America/New_York on the session date. The
updater excludes the current incomplete period and uses the ms-markets per-asset incremental update
range.

## Prerequisites

Apply project-owned schemas before running an update:

```bash
mainsequence migrations upgrade --provider src.migrations:migration head
```

Processes attach to those existing tables through `src.runtime.start_markets_engine()`; runtime
startup never creates a schema.

Register an Alpaca account by Main Sequence Secret names before updating prices:

```bash
alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper
```

## Discover and query datasets

```bash
alpaca-connectors market-data dataset list
alpaca-connectors market-data dataset get <meta-table-uid>
alpaca-connectors market-data prices \
  --dataset-uid <meta-table-uid> \
  --asset-uids <asset-uid> \
  --start 2025-01-01T00:00:00Z \
  --end 2025-12-31T23:59:59Z
```

Queries are bounded and compiled by the backend. They accept Asset UIDs or canonical asset
identifiers, never raw SQL, and return both identities on every observation.

## Create stored configurations

An `assets` source stores normalized membership rows. Create and update operations write the full
set with one bulk upsert and remove stale rows with one delete; they never issue one MetaTable
request per selected Asset:

```bash
alpaca-connectors market-data bar-configuration create \
  --name "Daily selected assets" \
  --account-uid <account-uid> \
  --asset-source assets \
  --asset-uids <asset-uid-1>,<asset-uid-2> \
  --frequency 1d \
  --feed sip \
  --adjustment all
```

A `universe` source resolves current members of one active `AssetUniverse`:

```bash
alpaca-connectors market-data bar-configuration create \
  --name "Daily managed universe" \
  --account-uid <account-uid> \
  --asset-source universe \
  --universe-uid <asset-universe-uid> \
  --frequency 1d \
  --feed sip \
  --adjustment all
```

An `account_holdings` source needs no asset list or universe. It selects the newest persisted
holdings set for the configured account inside the inclusive trailing 30-day UTC window, excludes
cash and zero positions, and never captures holdings as a side effect:

```bash
alpaca-connectors market-data bar-configuration create \
  --name "Daily account holdings" \
  --account-uid <account-uid> \
  --asset-source account_holdings \
  --frequency 1d \
  --feed sip \
  --adjustment all
```

List, inspect, update, and delete stored definitions with the other `market-data
bar-configuration` subcommands.

Before an update, Asset rows plus their Alpaca symbols and optional FIGIs are loaded with set-based
governed queries. The provider bars request is then grouped by incremental start time and sent in
symbol batches. No Main Sequence lookup or provider bars request is issued once per Asset.

## Resolve or execute updates

Dry-run resolution shows the current assets, output MetaTable, source snapshot (when applicable),
and update hash without resolving Secrets or contacting Alpaca:

```bash
alpaca-connectors market-data update \
  --configuration-uid <configuration-uid>
```

Execute that same stored definition with:

```bash
alpaca-connectors market-data update \
  --configuration-uid <configuration-uid> \
  --execute
```

The retained single-asset shorthand is also configuration-backed. It checks that the requested
ticker and period are present in the current resolution:

```bash
alpaca-connectors asset IVV update_prices daily \
  --configuration-uid <configuration-uid> \
  --execute
```

The account's stored Secret names are resolved only during execution, immediately before the
Alpaca clients are constructed. Credential values are never accepted or persisted by this flow.

## Run through the API and platform Job

The HTTP update action queues the branch-owned `Alpaca Bars Update` Job after the same read-only
configuration preflight:

```text
POST /v1/market-data/bar-configurations/{configuration_uid}/actions/update
```

Its path UID is the complete update input. The request cannot override the derived dataset,
account, asset source, frequency, feed, adjustment, Secret references, updater hash namespace, or
execution mode. A successful request returns `202 Accepted`, the Job and JobRun UIDs, and a
polling URL:

```text
GET /v1/operations/job-runs/{job_run_uid}
```

The Job launcher is `src/jobs/run_alpaca_bars_update.py` and its only argument is
`--configuration-uid <UUID>`. Configuration resolution and Alpaca execution remain in
`src.market_data.execute_market_data_update`; the launcher and API do not duplicate that logic.
