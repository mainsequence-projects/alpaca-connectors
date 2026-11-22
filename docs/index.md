# Alpaca Connectors

This project integrates Alpaca-backed assets, market data, accounts, holdings, order execution and
analytical portfolios with Main Sequence.

Current scope:

- register Main Sequence assets from Alpaca symbols under Alpaca's immutable asset UUID, with
  optional OpenFIGI enrichment and the trading calendar of each asset's class
- publish Alpaca stock bars through an ms-markets `AssetIndexedUpdater` (storage-first)
- register Alpaca brokerage accounts with application-managed or imported keys and capture
  holdings snapshots; the account sync sizes the account's target and allocates its funds
- place, sync and cancel batches of market orders on a registered account for authorised
  applications, writing the ms-markets execution ledger (ADR 0016)
- run durable scheduled analytical portfolios from weights an application provides, valued on
  persistent interpolated Alpaca bars with reusable persisted-calendar rebalance configurations
  (ADR 0024, ADR 0027)
- expose plans, executions, validation outcomes, and existing invocation paths

Market-domain behavior runs on **ms-markets** (`msm`) over `mainsequence`. See the migration
record at `docs/implementation_tasks/0001_ms_markets_storage_first_migration.md`.

## Repository Areas

- `src/alpaca_connectors/api/app/capabilities.py`: API-facing capability catalog
- `src/alpaca_connectors/assets/`: asset registration, FIGI enrichment, trading calendars
- `src/alpaca_connectors/market_data/`: Alpaca bar storage, update logic, and supporting functions
- `src/alpaca_connectors/account/`: Alpaca account registration and project-owned account details
- `src/alpaca_connectors/holdings/`: public account-holdings capability boundary
- `src/alpaca_connectors/execution/`: order batches, pre-trade checks and the execution ledger
- `src/alpaca_connectors/cli/`: thin capability command adapters
- `src/jobs/`: repository-local launchers for platform-managed execution
- `src/alpaca_connectors/operations/`: durable workflow state and runs on the workflow-declared
  portfolio Job
- `src/alpaca_connectors/portfolios/`: portfolios from provided weights with `msm_portfolios`

See [Capability Model](capabilities/index.md) before the workflow-specific pages.

## Main Decisions

- Assets are identified by Alpaca's immutable asset UUID (`ALPACA::<uuid>`, ADR 0004); FIGI is
  optional enrichment, never an identity. Every asset write names the trading calendar of the
  asset's Alpaca class (ADR 0026).
- Alpaca daily bars share one storage table per `frequency_id`, `feed`, and `adjustment`
  (`src/alpaca_connectors/market_data/storage.py`); the table identifier preserves the legacy
  `alpaca_stock_bars_<freq>_<feed>_<adjustment>` string. Physical table names include the triple
  concept, for example `bars_1d_iex_raw`; cadence carries the frequency and extra storage identity
  components carry only the non-cadence variant fields (`feed` and `adjustment`).
- Storage-first: schema lives on the storage class, not the DataNode config; tables must be
  migrated and the runtime attached (`start_markets_engine()`) before writes.
- Bar updates are configuration-first: users maintain UUID-keyed `AlpacaBarsConfiguration` rows
  with `assets`, recent `account_holdings`, or `portfolios` sources. The output MetaTable is
  resolved from frequency/feed/adjustment and is never a write input.
- One portfolio path (ADR 0027): a portfolio is built from weights an application provides, whose
  signal is ms-markets' `ApplicationWeights`, and the application owns it (ADR 0024). Portfolio
  construction uses the portfolio runtime and persistent `msm_portfolios.InterpolatedPrices` over
  the registered Alpaca bars table. The portfolio Job declared in
  `.mainsequence/workflows/alpaca-connectors-api.yaml` runs every configuration and owns schedule
  and compute; a configuration only chooses whether the daily scheduled run includes it
  (ADR 0013). Every delete retires the portfolio's `Portfolio` first.
- A daily bar's `time_index` is the close of its NYSE session: 16:00 America/New_York, or 13:00 on an early-close day.
- The FastAPI application authenticates every caller through the SDK request identity that
  `create_app()` installs; routes read the verified caller with `User.get_logged_user()`, and no
  route is public (ADR 0014).

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
- `alpaca-connectors asset link-calendars`
- `alpaca-connectors account register`
- `alpaca-connectors holdings capture`
- `alpaca-connectors market-data update`
- `alpaca-connectors market-data bar-configuration create`
- `alpaca-connectors asset <ticker> update_prices <period>`
- `alpaca-connectors portfolio rebalance create ...`
- `alpaca-connectors portfolio list`
- `alpaca-connectors portfolio run <CONFIGURATION_UID>`
- `POST /v1/portfolio-configurations/provided-weights`, the only way to create a portfolio
- `src/jobs/run_alpaca_bars_update.py` and its daily dispatcher
  `src/jobs/dispatch_alpaca_bars_update.py`, declared in
  `.mainsequence/workflows/alpaca-bars-update.yaml`
- `src/jobs/run_alpaca_portfolio.py` and its daily dispatcher `src/jobs/dispatch_alpaca_portfolio.py`,
  declared in `.mainsequence/workflows/alpaca-connectors-api.yaml`
