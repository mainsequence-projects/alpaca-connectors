# ADR 0029: Interpolated-Price Tables Are Named Like Every Project Table

## Status

Proposed on 2026-11-19. Waits for
[MainSequenceMarkets#95](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/95):
ms-markets must let a provider name its interpolated-price table from a key that is the same in
every Environment. Nothing in this repository changes until that release is out.

It amends the interpolated-price parts of
[ADR 0025](0025_markets_reads_the_instances_through_a_second_namespace.md) and of the
[Markets API Boundary Runbook](../operations/markets-api-boundary.md) (step 4).

## Context

Every MetaTable exists per Environment: each Environment has its own MetaTables, and the
migration Job creates this application's tables there on every push
([ADR 0012](0012_deployment_workflow_migrations_and_schema_gated_jobs.md)). That works because
every project table has a name fixed in code, such as `alpaca_connectors__bars_1d_sip_all`. One
committed revision creates it, and the migration Job applies the same revision in every
Environment.

The two interpolated-price tables are the exception. A portfolio is valued on interpolated
prices, one table per bars profile (SIP all and IEX raw, `ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE`).
ms-markets 4.0.1 builds that table itself:

- `InterpolatedPrices` refuses an `output_table` and always builds its table with
  `configured_interpolated_prices_storage` (`msm_portfolios/contrib/prices/updaters.py:796`,
  `:812`).
- The table name, its catalog identifier `InterpolatedPricesTS.<suffix>` and its storage hash all
  come from a hash of an identity that includes the source bars table's MetaTable UID
  (`msm_portfolios/time_index/prices/storage.py:124-166`). A provider chooses only the namespace
  and the app segment.

The bars table's UID is assigned when the migration first registers it in an Environment's
MetaTables, so it differs in every Environment, and so does the interpolated table's name. No
committed revision can name it for every Environment. This repository therefore grew a separate
path:

- `alpaca-connectors portfolio prepare-interpolated-prices` reads the bars tables' UIDs, generates
  an Alembic revision on the spot and runs `upgrade` through a second provider
  (`alpaca_connectors.portfolios.interpolated_prices_migration`) that shares this application's
  version table.
- Revisions `0011` and `0021` are the revisions it generated for Production, committed with
  Production's suffixes (`…__t_0e9255594b348d8c`, `…__t_43b7a7a637ae4df3`).
- The portfolio Job's runtime (`portfolio_job_runtime_models` in `runtime.py`) refuses to start
  until those tables are registered (`assert_interpolated_prices_registered`).

The consequences:

- Production needed a person to register the tables (runbook step 4).
- In any other Environment, revision `0021` creates two tables nothing writes, and the tables the
  portfolio runs need do not exist: every run refuses with "Persistent portfolio
  InterpolatedPrices storage is not migrated". The generated revision exists only in the
  checkout of whoever ran the command, and it collides with the next shared revision.
- The command runs `upgrade` against a hosted Environment, which AGENTS.md forbids: hosted schema
  changes come only from the migration Job.
- It is a manual deployment step, where every other part of the schema is applied by CI/CD.

## Decision

1. **A project table's name is fixed in code.** No table of this application is named from a
   value that differs per Environment, such as a MetaTable UID. One committed revision creates it
   and the migration Job creates it in every Environment.
2. **The interpolated-price tables are named after their bars profile.** Each bars profile's
   interpolated-price table is `alpaca_connectors__interpolated_prices__<profile>`, for example
   `alpaca_connectors__interpolated_prices__1d_sip_all`, in `alpaca-connectors` and
   `alpaca-connectors-markets` as today (ADR 0025). Its storage identity is built from the bars
   table's physical name, through the stable source key MainSequenceMarkets#95 adds; the source
   UID is still resolved at run time to read the bars.
3. **They are ordinary project tables.** Their models join `all_project_metatable_models()`, a
   normal revision creates them, and the migration Job registers them with the rest. A new bars
   profile adds its interpolated-price table in the same revision as its bars table.
4. **The per-Environment path is removed.** `prepare-interpolated-prices`, the second migration
   provider, `ALPACA_CONNECTORS_INTERPOLATED_PRICES_SOURCES`, the revision-file scan in
   `interpolated_prices_schema.py` and runbook step 4 go. Nothing in this repository generates a
   revision or runs `upgrade` outside the migration Job.

## Rollout

After the ms-markets release that resolves MainSequenceMarkets#95:

1. **Add, then switch.** One revision creates the two profile-named tables. The same release
   builds the storage with the stable key, so the portfolio runs write the new tables, and the
   migration Job registers them.
2. **Production's history.** The old tables hold every interpolated row so far. Either the
   release copies them into the new tables (a `TableSeriesMove` per profile, as
   `operations/instance_backfill.py` does for revision 0011's tables), or the first run
   interpolates the full history again from the bars, which it can do because the bars tables
   keep every row. The release that implements this ADR chooses one and records it here.
3. **Markets reads the new tables.** Each portfolio's dataset catalog row is registered again
   after its next run (`PortfolioDatasets.register`), so Markets follows the new instance in
   `alpaca-connectors-markets`. The migration Job's report (issue #37) lists any table Markets
   cannot read.
4. **Drop later.** A later revision drops `…__t_0e9255594b348d8c`, `…__t_43b7a7a637ae4df3` and
   revision 0011's `ms_markets__interpolated_prices__t_*` tables, once every Environment runs the
   new release (revisions add first and drop later).

## Consequences

- A new Environment runs portfolios after its first push, with no command to run.
- The interpolated tables follow the same rule as every other table, so a test can check that no
  project table name is derived at run time.
- The change depends on ms-markets. Until its release, Production keeps working as it does, and
  any other Environment still needs `prepare-interpolated-prices` once.
- Renaming the tables gives them new catalog identifiers and storage hashes, so the interpolation
  updater starts new update records for them. That is why the rollout copies the history or
  rebuilds it.
