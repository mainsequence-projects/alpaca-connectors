# 0001 - SDK 8 And ms-markets Storage-First Migration

> **Status:** implemented locally and applied to the backend on 2026-09-02.
> **Supported runtime:** Python 3.13, `mainsequence>=8.1.8`, `ms-markets>=1.0.13`.
> `pyproject.toml` intentionally uses compatible lower bounds; `uv.lock` and the exported
> `requirements.txt` provide the reproducible resolution.

## Goal and success criteria

Migrate the connector from the removed `mainsequence.tdag`/implicit-registration architecture to
SDK 8 and current ms-markets while preserving these workflows:

- provider-native Alpaca UUID registration with optional OpenFIGI enrichment
- ETF holdings extraction and `HOLDINGS__<ETF>` category membership refresh
- single-asset and category-scoped Alpaca OHLCV publication
- ETF-holdings portfolio construction
- thin FastAPI, CLI, and scheduled-job surfaces

The migration is successful when:

1. imports and tests run under Python 3.13 with current packages;
2. DataNodes declare storage with `_required_output_table()`;
3. project MetaTables are managed through a reviewed Alembic provider and are active at head;
4. runtime attachment resolves those already-migrated tables without registering schema;
5. the repository schedule uses the current backend workflow contract.

## Dependency and packaging changes

- Python moved to `>=3.13,<3.14`; `.python-version` is `3.13`.
- `mainsequence>=8.1.8` and `ms-markets>=1.0.13` are lower bounds, not exact pins.
- `uv.lock` is the source of reproducible dependency resolution.
- `requirements.txt` is exported from the lock for execution images.
- The Docker base moved to the Python 3.13 Main Sequence image family.

An SDK upgrade can intentionally move the lower bound and lock together:

```bash
mainsequence code-repository update-sdk --path .
mainsequence code-repository freeze-env --path .
```

## DataNode migration

`AlpacaStockBarsNode` is an `msm.data_nodes.assets.AssetIndexedDataNode`.

- Schema is declared on project-owned storage classes in `src/market_data/storage.py`.
- The node implements `_required_output_table()`; the removed `_required_storage_table()` and
  `storage_table` constructor/property surfaces are not used.
- Rows are keyed by `(time_index, asset_identifier)` where `asset_identifier` is
  `AssetTable.unique_identifier`.
- Each supported `(frequency, feed, adjustment)` triple has an explicit storage class and physical
  table.
- The published chart identifiers remain `alpaca_stock_bars_1d_sip_all` and
  `alpaca_stock_bars_1d_iex_raw`.

SDK 8 migration-managed catalog identifiers are the authored physical table names, not those
former DataNode identifiers. The API maps the published identifier to its storage class and then
resolves the catalog row by physical identity.

## Project-owned MetaTable migration

Provider: `src.migrations:migration`

- package: `src`
- migration namespace: `alpaca-connectors`
- version table: `alpaca_connectors__alembic_version`
- Alembic head: `0001`

Revision `0001` creates only:

- `alpaca_connectors__bars_1d_sip_all`
- `alpaca_connectors__bars_1d_iex_raw`
- `alpaca_connectors__acct_alpaca`

The target `MetaData` also contains `AssetTable`, `AccountGroupTable`, and `AccountTable` so
SQLAlchemy can resolve and sort foreign keys. A provider include hook excludes those dependency
tables from DDL, and they are excluded from `metatable_models`; core ms-markets schema remains
owned by the ms-markets provider.

The reviewed revision contains creates, indexes, and foreign keys only. It does not drop or alter
existing tables.

### Backend evidence

The migration was applied with:

```bash
mainsequence migrations upgrade --provider src.migrations:migration head
mainsequence migrations current --provider src.migrations:migration
```

The backend reported:

- revision `0001 (head)`
- four active managed resources (three project tables plus the Alembic registry)
- zero reserved resources
- zero failed resources
- physical tables present for every finalized resource

`msm.start_engine(models=all_project_metatable_models())` then resolved the three project models
and their three transitive core dependencies successfully.

## API migration

- FastAPI attaches the markets runtime through its lifespan handler.
- Route handlers remain thin and delegate asset registration and holdings-category execution to
  the reusable services under `src/`.
- The deprecated chart and presentation-specific API contracts were removed from this backend
  migration.

## Portfolio migration

ETF holdings signals continue to come from `etfhextractor`. Its current signal node uses the
ms-markets `_required_output_table()` contract.

`etfhextractor 0.4.x` is migrated as part of this backend change. Its demo node declares
`_required_output_table()`, registered price-table UIDs resolve through `TimeIndexTableRef`, and its
reusable `build_etf_tracking_portfolio(...)` owns calendar, signal, portfolio configuration,
portfolio row, and `PortfoliosDataNode` assembly. The Alpaca project passes its interpolated price
updater and explicit `...__ALPACA` identity into that builder; it does not duplicate or bypass the
dependency's portfolio functionality.

## Scheduling migration

The removed `scheduled_jobs.yaml` and `schedule_batch_jobs` path was replaced with:

`.mainsequence/workflows/daily-stock-bars-holdings-ivv.yaml`

The backend validated that document against workflow contract `2.1.0`. Repository workflow
application occurs on a committed/pushed CodeRepository event. No job existed in the backend at
migration time, so the workflow still needs the normal repository sync before a scheduled Job is
created.

## Data and operational implications

- The new project tables are migration-owned and start empty. The schema migration does not copy
  data from any removed legacy DataNode storage automatically.
- The backend had no `TimeIndexMetaTable` catalog row under the legacy public alias. New reads and
  writes therefore target the finalized project table through the explicit alias mapping.
- First production execution must be treated as a backfill/update of the new table and verified
  before downstream chart or portfolio consumers are switched to it.
- SDK 8 schema/hash changes can produce new update identities; compare planned hashes before
  forcing production runs.
- Runtime startup fails loudly when migrations are missing; it does not repair schema.
- An exact Python 3.13 execution image must be built from the committed dependency lock before a
  scheduled run.
- The backend currently has no CodeRepository image, and local registry credentials could not
  inspect the private Python 3.13 base manifest. The first platform image build must verify that
  base tag before job/API release.

The backend-validated workflow also remains unapplied until the migration changes are reviewed,
committed, and synchronized; the current dirty checkout was not committed or pushed automatically.

`etfhextractor 0.4.1` is published at commit
`036c8ba7f625f45dcb58e909ebf4eebbedbb5b97` with tag `v0.4.1`. This repository keeps the dependency
source unpinned in `pyproject.toml`; `uv.lock` and exported `requirements.txt` capture that exact
commit for reproducible builds.
