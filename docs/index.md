# Alpaca Connectors

This project publishes Alpaca US equity data into MainSequence.

Current scope:

- register MainSequence public assets from Alpaca symbols through FIGI
- extract ETF holdings from published provider sources
- create holdings-based `AssetCategory` objects such as `HOLDINGS__IVV`
- publish Alpaca stock bars through a MainSequence `DataNode`
- schedule recurring bar updates through `scheduled_jobs.yaml`

## Repository Areas

- `src/assets/`: asset registration, FIGI resolution, Alpaca universe checks
- `src/cli/`: project CLI commands for assets and holdings categories
- `src/jobs/`: repository-local job launchers for scheduled execution paths
- `src/extractors/`: ETF provider holdings extraction only
- `src/holdings_categories.py`: holdings-driven `AssetCategory` planning and sync
- `src/data_nodes/`: Alpaca stock bars `DataNode`
- `scripts/`: remaining helper entrypoints that are not yet in the CLI
- `data/seed_universes.yaml`: seed universes and ETF provider mapping

## Main Decisions

- Assets are registered in MainSequence by FIGI, not as custom assets.
- ETF holdings extraction is provider-driven and explicit.
- Component extraction only happens when both a seed ticker and provider are supplied.
- Holdings categories are strict: missing Alpaca symbols, missing FIGI, or missing registered assets block creation.
- Alpaca daily bars share one table per `frequency_id`, `feed`, and `adjustment`.
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
- `scheduled_jobs.yaml`
