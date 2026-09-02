# Alpaca Connectors

This project publishes Alpaca US equity data into MainSequence.

Current scope:

- register MainSequence public assets from Alpaca symbols through FIGI
- expand ETF holdings through the external `etfhextractor` dependency
- create holdings-based `AssetCategory` objects such as `HOLDINGS__IVV`
- build ETF-tracking portfolios from ETF holdings signals and interpolated Alpaca bars
- publish Alpaca stock bars through an ms-markets `AssetIndexedDataNode` (storage-first)
- schedule recurring bar updates through `.mainsequence/workflows/`

Market-domain behavior runs on **ms-markets** (`msm`) over `mainsequence`. See the migration
record at `docs/implementation_tasks/0001_ms_markets_storage_first_migration.md`.

## Repository Areas

- `src/assets/`: asset registration, FIGI resolution, Alpaca universe checks
- `src/cli/`: project CLI commands for assets and holdings categories
- `src/jobs/`: repository-local job launchers for scheduled execution paths
- `src/etf_holdings.py`: local adapter over `etfhextractor` for seed expansion, provider defaults,
  and holdings-category orchestration
- `src/data_nodes/`: Alpaca stock bars `DataNode`
- `src/portfolios/`: ETF holdings portfolio construction with `msm_portfolios`
- `scripts/`: remaining helper entrypoints that are not yet in the CLI

## ETF Extraction Documentation

ETF extraction has its own standalone architecture page:

- `docs/etf_extraction.md`

Use that page first when the question is about:

- provider extraction behavior
- seed expansion
- ETF-owned holdings category sync
- the boundary between ETF logic and Alpaca logic

## Main Decisions

- Assets are registered in MainSequence by FIGI, not as custom assets.
- ETF holdings extraction is delegated to `etfhextractor` and remains provider-driven and explicit.
- Component extraction only happens when both a seed ticker and provider are supplied.
- Holdings categories are ETF-owned: ETF extraction defines category membership, and only MainSequence asset registration ambiguity/missing assets block category sync.
- Alpaca daily bars share one storage table per `frequency_id`, `feed`, and `adjustment`
  (`src/markets_storage/`); the table identifier preserves the legacy
  `alpaca_stock_bars_<freq>_<feed>_<adjustment>` string. Physical table names include the triple
  concept, for example `bars_1d_iex_raw`; cadence carries the frequency and extra storage identity
  components carry only the non-cadence variant fields (`feed` and `adjustment`).
- Storage-first: schema lives on the storage class, not the DataNode config; tables must be
  migrated and the runtime attached (`start_markets_engine()`) before writes.
- Portfolio construction uses the portfolio runtime (`start_portfolio_markets_engine()`), an ETF
  holdings signal from `etfhextractor`, and `msm_portfolios.InterpolatedPrices` over the registered
  Alpaca bars table.
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
- `alpaca-connectors holdings-category create`
- `alpaca-connectors bars run`
- `alpaca-connectors asset <ticker> update_prices <period>`
- `src/jobs/run_daily_stock_bars_holdings_ivv.py`
- `.mainsequence/workflows/daily-stock-bars-holdings-ivv.yaml`
