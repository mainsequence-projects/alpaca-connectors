# ADR 0029: Interpolated-Price Tables Are Named Like Every Project Table

## Status

Accepted and implemented on 2026-11-22, on ms-markets 5.0.0, the release that resolves
[MainSequenceMarkets#95](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/95).
Proposed on 2026-11-19. ms-markets decided how a producer names the table differently from what
this ADR first proposed; decision 2 records what it decided (see [History](#history)).

It was released on 2026-11-23 on ms-markets 5.2.0, which fixes
[MainSequenceMarkets#118](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/118)
and [#119](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/119) (see
[Rollout](#rollout)).

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

The two interpolated-price tables were the exception. A portfolio is valued on interpolated
prices, one table per bars profile (SIP all and IEX raw, `ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE`).
Up to ms-markets 4.2, `InterpolatedPrices` refused an `output_table` and built its table itself,
named from a hash that included the source bars table's MetaTable UID. That UID differs in every
Environment, so no committed revision could create the table. This repository therefore had a
separate path:

- `alpaca-connectors portfolio prepare-interpolated-prices` read the bars tables' UIDs, generated
  an Alembic revision on the spot and ran `upgrade` through a second provider that shared this
  application's version table.
- Revisions `0011` and `0021` committed two such tables, `…__t_0e9255594b348d8c` and
  `…__t_43b7a7a637ae4df3`. Their names match no Environment's bars tables today.
- The portfolio Job refused to start until the tables its Environment needed were registered.

In Production they never were: every portfolio run failed with "Persistent portfolio
InterpolatedPrices storage is not migrated", and the command that would have created them runs
`upgrade` against a hosted Environment, which AGENTS.md forbids.

## Decision

1. **A project table's name is fixed in code.** No table of this application is named from a
   value that differs per Environment, such as a MetaTable UID. One committed revision creates it
   and the migration Job creates it in every Environment.
2. **Each bars profile has one interpolated-price table, keyed by its configuration.** It is the
   `InterpolatedPricesTS` instance that ms-markets' `configured_time_index_instance` builds with
   the key `interpolated_prices_instance_key` computes from the bars table's physical name, its
   cadence, the upsample frequency and the rule (`1d` and `ffill`, the only values a portfolio
   configuration accepts). `alpaca_connectors.instances.INTERPOLATED_PRICES_BY_PROFILE` declares
   them, in `alpaca-connectors`, on this application's base:

   | Bars profile | Interpolated-price table |
   | --- | --- |
   | 1d SIP all | `alpaca_connectors__interpolatedpricests__t_1330c8b4c5fd5124` |
   | 1d IEX raw | `alpaca_connectors__interpolatedpricests__ff97574884b78031` |

   ms-markets chose the key, not a name a producer picks, so that another configuration is
   another table by construction: rows are keyed only by time and asset.
3. **They are ordinary project tables.** They join `all_project_metatable_models()`, revision
   `0034` creates them, and the migration Job registers them with the rest. A portfolio run binds
   its profile's table as `InterpolatedPrices(..., output_table=...)`, and ms-markets refuses a
   class of another configuration. A new bars profile adds its interpolated-price table in the
   revision that adds its bars table.
4. **The per-Environment path is removed.** `prepare-interpolated-prices`, the second migration
   provider, `ALPACA_CONNECTORS_INTERPOLATED_PRICES_SOURCES`, the revision-file scan and runbook
   step 4 are gone. Nothing in this repository generates a revision or runs `upgrade` outside the
   migration Job.
5. **Every table of this application is on its own declarative base.** ms-markets 5.0.0 requires
   the instance factories' `namespace`, `storage_app` and `base`, and keys a producer's tables to
   Markets by Markets' own columns. `alpaca_connectors.base.ProjectBase` holds every table this
   application owns, the provider targets its metadata, keys to Markets pass column objects, and
   the provider needs no include hook. The 18 existing tables keep their names, identifiers and
   DDL, and autogenerate finds nothing to change in them.

## Rollout

1. **One release.** It requires ms-markets 5.2.0 and MetaTables 0.1.74. Revision `0034` creates
   the two tables, and the portfolio Job writes them from its first run. Nothing is copied:
   Production never registered an interpolated-price table, so there is no history to keep, and
   the first run interpolates from the updater's start, 2017-07-20, which is before the first
   bar this application stores (2018-01-02).
2. **Pushed only on ms-markets 5.2.0, which fixes
   [#118](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/118).** Before it, in
   a shared rebalance-state table, a new portfolio took another portfolio's progress as its own and
   recorded nothing, or a cut history. Fixing this table without it would have turned a failing
   Job into silently wrong portfolios. The lifecycle check's portfolio Job stage runs three
   portfolios on the shared tables to catch it
   ([Development Workflow](../operations/development.md)). 5.2.0 also fixes
   [#119](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/119), the same
   defect for the calendar events of a second calendar.
3. **Drop later.** A later revision drops the four orphaned tables, `…__t_0e9255594b348d8c` and
   `…__t_43b7a7a637ae4df3` of revisions 0011 (`ms_markets__…`) and 0021 (`alpaca_connectors__…`),
   with `op.drop_table(name, if_exists=True)`, once every Environment runs this release (revisions
   add first and drop later). No code reads them and no catalog row names them.

## Consequences

- A new Environment runs portfolios after its first push, with no command to run.
- The interpolated tables follow the same rule as every other table: a test checks that every
  project table is on `ProjectBase` and that each profile's table has the name ms-markets derives
  from its configuration.
- A portfolio configuration with another upsample frequency or rule would need another table and
  revision; the configuration refuses any other value today.
- Every portfolio run writes the same table for its profile; each asset's interpolated history
  depends only on its own bars, so the portfolios that share an asset share its rows.

## History

On 2026-11-19 this ADR proposed tables named after their bars profile
(`alpaca_connectors__interpolated_prices__1d_sip_all`), with ms-markets building the storage
identity from a stable source key. ms-markets' owner decided instead (MainSequenceMarkets#95,
2026-11-21 and 2026-11-22) that the key is computed from the configuration, that
`InterpolatedPrices` requires `output_table=`, and that no producer picks a name. Decision 2 is
that design.
