# Alpaca Connectors

This project integrates Alpaca-backed assets, universes, market data, accounts, holdings, and
analytical portfolios with Main Sequence.

Current scope:

- register MainSequence public assets from Alpaca symbols through FIGI
- maintain durable extraction sources and materialize provider-derived asset universes
- publish Alpaca stock bars through an ms-markets `AssetIndexedDataNode` (storage-first)
- register Alpaca brokerage accounts by Secret names and capture holdings snapshots independently
- build durable scheduled analytical portfolios from existing ETF Weight Signals, persistent
  interpolated Alpaca bars, and reusable ImmediateSignal rebalance configurations
- expose plans, executions, validation outcomes, and existing invocation paths

Market-domain behavior runs on **ms-markets** (`msm`) over `mainsequence`. See the migration
record at `docs/implementation_tasks/0001_ms_markets_storage_first_migration.md`.

## Repository Areas

- `api/app/capabilities.py`: API-facing capability catalog
- `src/assets/`: asset registration, FIGI resolution, Alpaca universe checks
- `src/universes/`: source CRUD and Asset Universe execution across extraction, bulk registration,
  category membership, and observed signal weights
- `src/market_data/`: Alpaca bar storage, update logic, and supporting functions
- `src/account/`: Alpaca account registration and project-owned account details
- `src/holdings/`: public account-holdings capability boundary
- `src/cli/`: thin capability command adapters
- `src/jobs/`: repository-local launchers for platform-managed execution
- `src/operations/`: durable workflow state and one-configuration-per-Job reconciliation
- `src/portfolios/`: ETF holdings portfolio construction with `msm_portfolios`

See [Capability Model](capabilities/index.md) before the workflow-specific pages.

## ETF Extraction Documentation

ETF extraction has its own standalone architecture page:

- `docs/etf_extraction.md`

Use that page first when the question is about:

- provider extraction behavior
- configured provider extraction
- source-owned holdings membership refresh
- the boundary between ETF logic and Alpaca logic

## Main Decisions

- Assets are registered in MainSequence by FIGI, not as custom assets.
- ETF holdings extraction is delegated to `etfhextractor`; managed extraction targets are
  `UniverseSource` rows, not Python constants.
- Holdings categories are source-owned: Universe Run resolves current ETF holdings and weights,
  registers missing Alpaca-backed constituents through the account selected for that execution,
  changes membership only after the complete constituent set is available, and publishes that same
  extraction through `AlpacaETFHoldingsSignal`. Universes do not own accounts.
- Alpaca daily bars share one storage table per `frequency_id`, `feed`, and `adjustment`
  (`src/market_data/storage.py`); the table identifier preserves the legacy
  `alpaca_stock_bars_<freq>_<feed>_<adjustment>` string. Physical table names include the triple
  concept, for example `bars_1d_iex_raw`; cadence carries the frequency and extra storage identity
  components carry only the non-cadence variant fields (`feed` and `adjustment`).
- Storage-first: schema lives on the storage class, not the DataNode config; tables must be
  migrated and the runtime attached (`start_markets_engine()`) before writes.
- Bar updates are configuration-first: users maintain UUID-keyed `AlpacaBarsConfiguration` rows
  with `assets`, `universe`, or recent `account_holdings` sources. The output MetaTable is resolved
  from frequency/feed/adjustment and is never a write input.
- Portfolio construction uses the portfolio runtime (`start_portfolio_markets_engine()`), the
  connector-owned Universe-backed `AlpacaETFHoldingsSignal`, and
  persistent `msm_portfolios.InterpolatedPrices` over the registered Alpaca bars table. Durable
  Portfolio Configurations reference existing Signal, Bars, and Rebalance Configurations; their
  dedicated Jobs exclusively own schedule and compute state.
- Daily bar `time_index` is normalized to `16:00 America/New_York` on the session date as a project convention.

## Build The Docs

```bash
uv run mkdocs serve
```

or:

```bash
uv run mkdocs build
```

## Related Entry Points

- `alpaca-connectors asset register`
- `alpaca-connectors universe run <UNIVERSE_UID>`
- `alpaca-connectors universe-source list`
- `alpaca-connectors account register`
- `alpaca-connectors holdings capture`
- `alpaca-connectors market-data update`
- `alpaca-connectors market-data bar-configuration create`
- `alpaca-connectors asset <ticker> update_prices <period>`
- `alpaca-connectors signal create ...`
- `alpaca-connectors signal run <CONFIGURATION_UID>`
- `alpaca-connectors portfolio rebalance create ...`
- `alpaca-connectors portfolio create ...`
- `alpaca-connectors portfolio run <CONFIGURATION_UID>`
- `src/jobs/run_alpaca_bars_update.py`
- `.mainsequence/workflows/alpaca-bars-update.yaml`
