# Alpaca historical prices

## Goal

Publish and query Alpaca stock OHLCV bars in migration-managed ms-markets asset-indexed
MetaTables. Every update starts from a durable configuration that the user can review before it
runs. The user never selects an output dataset UID for an update.

Main modules:

- `src/alpaca_connectors/market_data/storage.py`: migrated table contracts
- `src/alpaca_connectors/market_data/configurations.py`: stored configuration and explicit-asset membership tables
- `src/alpaca_connectors/market_data/alpaca_bars.py`: `AssetIndexedUpdater` producer
- `src/alpaca_connectors/market_data/services.py`: configuration resolution, dataset catalog, reads, and execution
- `src/alpaca_connectors/cli/bars.py`: thin CLI adapter

## Data contract

The grain is `(time_index, asset_identifier)`. `asset_identifier` is the canonical ms-markets
`Asset.unique_identifier`, with a foreign key to `AssetTable.unique_identifier`. Observations
contain `open`, `high`, `low`, `close`, `volume`, `trade_count`, and `vwap`.

The currently migrated profiles are:

- `1d/iex/raw`: daily IEX-feed bars with raw, unadjusted prices.
- `1d/sip/all`: daily consolidated SIP-feed bars adjusted for all corporate actions.

The `all` token is Alpaca's adjusted mode. Alpaca also defines `split` and `dividend` adjustment
modes, but they are not selectable here because this repository does not yet contain migrated
storage profiles for those exact triples.

Frequency, feed, and adjustment are stored on the configuration. Their triple resolves exactly one
output storage class. A request cannot create a new combination dynamically; adding one requires a
storage class and an Alembic migration.

For daily bars, `time_index` is the close of the bar's NYSE session, the calendar a US equity is
linked to (ADR 0026): 16:00 America/New_York, or 13:00 on an early-close day, and `open_time` is
the session's open. ms-markets puts daily bars on the session closes of the asset's calendar, so a
bar stamped at 16:00 on an early-close day fell between two sessions and interpolation valued that
day at the previous close. Revision `0035` moved the bars stored before this rule on the 2018 to
2026 early closes. The updater excludes the current incomplete period and uses the ms-markets
per-asset incremental update range.

## Prerequisites

Apply project-owned schemas before running an update:

```bash
metatables --local migrations upgrade --provider alpaca_connectors.migrations:migration head   # hosted: deployment migration Job
```

Processes attach to those existing tables through `alpaca_connectors.runtime.start_markets_engine()`; runtime
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

A bar configuration has exactly one parent
([ADR 0028](../adrs/0028_every_portfolio_belongs_to_one_account.md), decision 4):

- **A bars feed** belongs to an account: its keys fetch an explicit asset list, the account's
  holdings, or, as before ADR 0028, the assets of the portfolios not yet adopted that name it
  (`portfolios`), and it is deleted with the account. It may not run with another owner's account;
  an account without an owner stays open (ADR 0022).
- **A portfolio's own configuration** belongs to its portfolio: it records the portfolio's profile
  and whether its bars are enabled, and it is created and deleted with the portfolio
  ([Portfolios](../portfolios/portfolios.md#bars)). Its source is `portfolios`.

A `portfolios` configuration, a portfolio's own or a feed, runs as its account's `AccountStockBars`
of its profile ([ADR 0033](../adrs/0033_an_accounts_portfolios_share_one_price_chain.md)): every
asset the signals of the account's live portfolios on that profile ever published, read from the
signal-asset table rather than the signal weights series, with the account's keys. The
configuration and the profile are its whole update hash, so the account's bars keep one update
record, and a portfolio run's graph runs the same updater. It fetches nothing when every asset
already holds its last final daily bar.

Either kind writes the one shared price table of its bar profile, whose rows are keyed by time and
asset and name no account; deleting an account deletes its configurations, never price rows. A
feed that portfolios not yet adopted name is not deleted while they do (`409 action_conflict`), as
their key to it refused before ADR 0028.

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

An `account_holdings` source needs no asset list. It selects the newest persisted
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

The account's referenced Secrets are resolved by UID only during execution, immediately before
the Alpaca clients are constructed. Credential values are never accepted or persisted by this flow.

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
`alpaca_connectors.market_data.execute_market_data_update`; the launcher and API do not duplicate that logic.
