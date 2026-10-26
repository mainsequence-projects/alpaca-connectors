# ADR 0018: Markets Through The Markets API, Own Tables In `alpaca-connectors`

## Status

Accepted on 2026-10-26. Applies ms-markets ADR 0047 ("Markets API as the application boundary")
to this application. Requires ms-markets 3.0. Implementation status: implemented in the
connector; each Environment that ran an earlier release follows the
[Markets API Boundary Runbook](../operations/markets-api-boundary.md) once.

## Context

The connector used ms-markets as a library against the Markets tables: it compiled SQL on
`AssetTable`, `AccountTable`, `AccountHoldingsStorage`, the category and portfolio tables, ran the
ms-markets snapshot and holdings updaters in-process, and wrote its signals, portfolios, orders and
trades into the shared `ms_markets__*` time series. Its own thirteen tables inherited the
`mainsequence.markets` namespace from the ms-markets mixins. Its workloads therefore held
`data_writer` on the whole Markets namespace plus table `editor` roles, and a change of the Markets
schema could break it.

ms-markets ADR 0047 withdraws that pattern: registries are written through the Markets API under a
registry write policy, providers keep their own tables and time-series instances in their own
namespace, and Markets finds provider time series through its dataset catalog.

## Decision

1. **Namespace.** The project mixins declare `alpaca-connectors`
   (`markets_configured_namespace`), including the two bars storages. Existing tables move once
   with the MetaTables table patch, which keeps their uid; physical names, authored identifiers,
   updater hashes and signal uids do not change, and catalog identifiers gain the
   `alpaca-connectors.` prefix.
2. **Composition root.** Every entrypoint selects ms-markets' remote row backend
   (`LazyRemoteBackend`) and attaches only this project's tables; Markets tables are foreign-key
   targets by physical name. Own row classes list own tables in `__required_tables__`.
3. **Registries through the API.** Assets, asset types and OpenFIGI details use set-based row
   calls (`upsert_many`, batched `filter`); asset snapshots and holdings use the publication
   composites; categories, memberships, accounts, calendars, portfolios, signal metadata and order
   managers use row calls. Reads that joined a Markets table now query own tables and fetch the
   page's Markets rows in batches of 500. `ALPACA::<uuid>` identifiers are derived locally.
4. **Account listing.** Revision 0019 copies the Account's name and active flag onto
   `alpaca_connectors__acct_alpaca`, so the registration listing pages, filters and orders on it
   alone; reads still return the Account's own values. Markets never gets a role on, or
   `include` of, that table: it holds the application-encrypted keys.
5. **Own time series.** Signal weights, portfolio values, weights, rebalance state, calendar
   events, orders and trades go to contract instances built by the ms-markets factories
   (`alpaca_connectors.instances`, revision 0020); interpolated prices to namespaced tables
   (revision 0021). Producers are bound with `output_table=` and no hash namespace, so no identity
   changes. Each portfolio run registers and reconciles its dataset catalog row and each signal run
   binds its signal (`PortfolioDatasets`). The Markets workload reads the three catalogued
   instances with `data_viewer`.
6. **History.** `alpaca-connectors instances backfill` moves this application's rows of the
   shared time series into the instances once with ms-markets' shared series moves
   (`SignalSeriesMove`, `PortfolioSeriesMove`, `TableSeriesMove`), which verify every key before
   the catalog is written; `instances rollback` undoes it until the Markets roles are revoked.
   Signal observations are live ETF extractions and are copied, never recomputed. A person runs
   both; the selection comes from this application's tables.
7. **Schema gate.** Jobs no longer read the ms-markets Alembic version table; they wait until the
   Markets API's `/health` reports at least the ms-markets release of the image.

## Consequences

- No workload holds a role on `mainsequence.markets`; the workloads that write Markets are listed
  per resource in `MARKETS_REGISTRY_WRITERS`, and every resource that calls Markets declares
  `reader` on the `MainSequenceMarkets` branch.
- Listing every holdings row of an account reads all its snapshots through the API (50 per call)
  because Markets offers no paged read of holdings rows across sets; universe member counts read
  the page's memberships because Markets has no grouped count.
- Markets has no catalog for orders and trades instances yet, so a Markets read of orders or
  trades sees only the shared tables, which keep the rows written before the move.
- `etfhextractor` stays pinned as it was; its `ensure_trading_calendar` already uses Calendar row
  calls, which run on the Markets API.
