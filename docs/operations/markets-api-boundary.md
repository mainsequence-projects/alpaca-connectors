# Markets API Boundary Runbook

ms-markets ADR 0047 makes the Markets API the boundary between this application and Markets:
Markets registries (assets, asset types, accounts, holdings, categories, calendars, portfolios,
signal metadata, order managers) are read and written through the Markets API, the time series
this application produces live in its own `alpaca-connectors` namespace, and no workload of this
application holds a role on `mainsequence.markets`. This page lists, in order, what people do in
each Environment to move an existing deployment there. Nothing here runs from code, a Job or an
agent session; every command below is run by the person the step names.

## What Changes

| Before | From this release |
|---|---|
| Asset types, assets, OpenFIGI details compiled as SQL on Markets tables | `AssetType`/`Asset`/`OpenFigiDetails.upsert_many` and batched `filter` row calls |
| Asset snapshots and holdings written by in-process ms-markets updaters | `AssetSnapshots.publish`, `AccountHoldingsSnapshots.publish`/`get_many` |
| Account registration and holdings sync: asset type, asset and account upserts, `AssetSnapshots.publish`, the `USD` Asset ensured, `AccountHoldingsSnapshots.publish` | one `registerAccount` call (`POST /api/v1/account/registration/`, ms-markets 3.4.0, ADR 0051) |
| No target read and no fund allocation | the account sync reads the account's target and plans and allocates each snapshot it publishes: `getAccountTargetPositions`, `planAccountVirtualFunds` and `allocateAccountVirtualFunds` (ms-markets 3.7.0, ADR 0054; [ADR 0023](../adrs/0023_account_sync_sizes_and_allocates_funds.md)) |
| Registration listing joined `AccountTable` | pages `alpaca_connectors__acct_alpaca` (revision 0019 adds the account name and active flag) and reads the page's Accounts in one batch |
| Signal weights, portfolio values, weights, rebalance state, calendar events, orders, trades in `ms_markets__*` | `alpaca_connectors__<contract>__main` instances (revision 0020) |
| Interpolated prices in `ms_markets__interpolated_prices__t_*` (Markets namespace) | `alpaca_connectors__interpolated_prices__t_*` (revision 0021) |
| Portfolio reads of Markets tables only | each portfolio's Markets dataset catalog row points at the instances (`PortfolioDatasets.register` after every run; `register_signal` after every publication of weights) |
| Signal weights, portfolio weights and values in this application's instances, which Markets read through its catalog | published to Markets' own tables and read back with the ms-markets recipes; no catalog row, no second namespace ([ADR 0031](../adrs/0031_markets_series_are_published_not_shared.md)) |
| Job schema gate read the ms-markets Alembic version table | waits until the Markets API's `/health` reports at least the ms-markets release of the image |
| 13 project tables in `mainsequence.markets` | in `alpaca-connectors` (step 7) |
| Orders and trades written by the ms-markets `Orders` and `Trades` updaters, an update execution each, keyed by the order's identifier | the batch with its `Order` rows in one `OrderManager.create_batch(orders=...)` call, then the states and fills in one `write_execution` transaction, keyed by the order's uid (ms-markets 6.0.0, its ADR 0064; revision 0036, which gives the orders recorded before it their `Order` rows with `Order.upsert_many`) |
| An account delete deleted its orders and trades by account | its order states and events by the uids of its `Order` rows (`Order.filter`), its trades by account |
| A portfolio's values from `PortfoliosUpdater`, over the rebalance book; instances built by the ms-markets factories | every portfolio's ledger (`PortfolioEngine`) in `alpaca_connectors__portfolioeventledgerts__main` (revision 0037), whose projections are the published weights and values, and every instance a class statement in `alpaca_connectors.instances` (ms-markets 6.1.0 and 6.2.0, its ADRs 0065 and 0066) |

Identities do not change: the uid of every table, the physical names of the project tables, every
`update_hash` (output tables and catalog namespaces do not enter it, and no hash namespace is
passed) and every signal uid. Catalog identifiers of moved tables gain the `alpaca-connectors.`
prefix (step 7).

## 0. Prerequisites On The Markets Side

These belong to the Markets deployment's owners, not to this repository:

- ms-markets 3.0.2 is released, and the Environment's Markets deployment has applied its revision
  `0022` and deployed the `api` release that serves `/api/v1/rows/` and the portfolio dataset
  catalog routes.
- Account registration and holdings sync need ms-markets 3.4.0 or later on the Markets API,
  which serves `registerAccount` from that release.
- Sizing and allocating an account's target at the sync needs ms-markets 3.7.0 or later on the
  Markets API, which states the account target as exposure and takes the sized units with the
  allocation.

## 1. Roles On `alpaca-connectors`

The workloads get their roles from the migration, not from a person
([ADR 0030](../adrs/0030_workloads_get_their_roles_through_their_branch.md)). Both migration
providers declare `editor` on `alpaca-connectors` for every workload of this repository's branch:
the API, the Agent, the migration Job, the bars and portfolio Jobs and both dispatchers. The
declaration runs on every push, so a renamed or recreated workload keeps its role. Markets holds
no role on this application ([ADR 0031](../adrs/0031_markets_series_are_published_not_shared.md)).

The migration Job itself needs `editor` on the namespace before it can declare. It creates the
namespace when it does not exist yet and holds `editor` on it. On a namespace that already exists,
the owner (a person) grants it `editor` once, directly or through its Team. After that, the branch
binding covers its replacements. The person who runs step 4 needs the same role.

No workload needs a role on `mainsequence.markets` once step 9 is done.

## 2. Hold The Scheduled Runs

A portfolio run before step 6 would start from instances that do not hold the history that step
moves. Either push right after the 02:00 UTC portfolio dispatch and finish step 6 before the next
one, or take every portfolio off the schedule first
(its owner's `PATCH /v1/portfolio-configurations/{uid}` with `{"scheduled": false}`) and put it back in step 8. Until the
backfill, portfolio preflight blocks a run whose signal instance has no observation. Keep the runs
held while a move or a rollback runs.

Since ADR 0021 the bars dispatcher also starts a run for every enabled, scheduled bar
configuration at 00:00 UTC, and those runs write the bars tables that step 7 moves. Finish the move
between 00:00 UTC runs, or take each one off the schedule
(`alpaca-connectors market-data bar-configuration update <uid> --no-scheduled`) and put it back in
step 8. An unscheduled configuration keeps its portfolios linked, unlike a disabled one.

## 3. Push This Release

The deployment workflow's migration Job applies revisions `0019` (account listing columns),
`0020` (the time-series instances, registered in `alpaca-connectors`) and `0021` (the namespaced
interpolated-price tables); the API and the Agent deploy after it; the Jobs take the new image and
their schema gate waits until the Markets API reports the installed ms-markets release (3.0.2) or later. The workflow
grants every resource that calls Markets `reader` on the `MainSequenceMarkets` branch.

## 4. Interpolated-Price Tables

Nothing to do. Since
[ADR 0029](../adrs/0029_interpolated_price_tables_are_named_like_every_project_table.md)
(ms-markets 5.0.0) revision `0034` creates the interpolated-price table of each bars profile and
the migration Job registers it, in every Environment. The tables of revisions 0011 and 0021 are
named from bars-table UIDs, no code reads them, and a later revision drops them.

## 5. Markets Reads No Table Of This Application

Since [ADR 0031](../adrs/0031_markets_series_are_published_not_shared.md) this application
publishes signal weights, portfolio weights and portfolio values to Markets with the ms-markets
recipes, and reads them back from Markets; Markets reads no table of it. No table is in a second
namespace, nothing is registered in the Markets dataset catalog, and no person grants Markets
anything: a new Environment needs only the `reader` on the `MainSequenceMarkets` branch that the
workflow declares (step 3). Never grant Markets a role on a table or namespace of this application.

An Environment that runs the release before ADR 0031 moves to it with ADR 0031's
[Rollout](../adrs/0031_markets_series_are_published_not_shared.md#rollout), in one release. The
portfolios that exist before it are deleted, not carried across:

1. The Markets API runs ms-markets 4.2.0.
2. Hold the portfolio runs (step 2), and ask the applications that create portfolios to wait.
3. Delete every existing portfolio with this application's portfolio delete: its owner with
   `DELETE /v1/portfolio-configurations/{uid}`, or the operator with:

   ```bash
   alpaca-connectors portfolio list                          # every configuration UID
   alpaca-connectors portfolio delete <uid>                  # dry run
   alpaca-connectors portfolio delete <uid> --execute
   ```

   The delete retires the `Portfolio`, which needs no manager, so the rows created before
   ms-markets ADR 0060 need no assignment, and deletes the configuration, its own bars
   configuration, its inline rebalance configuration and its signal definition. A portfolio an
   account's current target names is refused: that account's owner changes the target first.
4. Push this release. The migration Job applies revision 0033, which drops the three instances
   with every old signal weight, weight and value, and removes their memberships of
   `alpaca-connectors-markets`, which the code no longer declares.
5. Release the runs and the applications (step 8); they create their portfolios again as usual.
   Step 9 then removes the namespace.

A run of this release publishes the calendar's `market_open` and `market_close` into the private
calendar-events table by itself, since both strategies read them (ADR 0031, decision 5); the rows
already there are written again with equal values, and nobody runs a step for them.

## 6. Move The History

Before [ADR 0031](../adrs/0031_markets_series_are_published_not_shared.md) this step moved the
history from the shared `ms_markets__*` time series into this application's instances, with the
`instances backfill` command and ms-markets' series moves. Since that ADR the signal weights,
portfolio weights and values are published to Markets, the portfolios that existed before it are
deleted (step 5) and revision 0033 drops their instances; the command, its rollback and the series
moves are gone. The rebalance state, calendar
events, interpolated prices, orders and trades it moved stay in this application's instances.

As a person who holds `data_writer` on `alpaca-connectors`, copy each registration's account name
and active flag (read through the Markets API) onto its details row:

```bash
alpaca-connectors account backfill-listing-fields             # dry run
alpaca-connectors account backfill-listing-fields --execute
```

## 7. Move The Project Tables To `alpaca-connectors`

Until this release the project mixins inherited the `mainsequence.markets` namespace from
ms-markets, so all thirteen project tables were registered there. The mixins in
`alpaca_connectors.metatables` now declare `alpaca-connectors`; tables that a fresh Environment
creates are registered there directly. Existing tables are moved once by adding them to
`alpaca-connectors`, removing them from `mainsequence.markets` and patching their identifier
(MetaTables 0.1.66 or later, its ADR 0028; the table patch no longer takes a namespace):

- the table's `uid`, its physical name, its rows, its foreign keys and its update records do not
  change, and the account keys need no re-encryption (the keyring seals values, not catalog
  names);
- updater identities do not change: the bars updaters bind their output table by physical name,
  and `update_hash` does not include the table's namespace or identifier;
- the catalog identifier gains the namespace prefix ms-markets gives every table outside the
  Markets namespace (`markets_identifier`):

| Physical table | Identifier before | Identifier after |
|---|---|---|
| `alpaca_connectors__asset_alpaca` | `AlpacaAssetDetails` | `alpaca-connectors.AlpacaAssetDetails` |
| `alpaca_connectors__bars_1d_sip_all` | `alpaca_stock_bars_1d_sip_all` | `alpaca-connectors.alpaca_stock_bars_1d_sip_all` |
| `alpaca_connectors__bars_1d_iex_raw` | `alpaca_stock_bars_1d_iex_raw` | `alpaca-connectors.alpaca_stock_bars_1d_iex_raw` |
| `alpaca_connectors__acct_alpaca` | `AlpacaAccountDetails` | `alpaca-connectors.AlpacaAccountDetails` |
| `alpaca_connectors__account_key_request` | `AccountKeyRequest` | `alpaca-connectors.AccountKeyRequest` |
| `alpaca_connectors__bars_configuration` | `AlpacaBarsConfiguration` | `alpaca-connectors.AlpacaBarsConfiguration` |
| `alpaca_connectors__bars_configuration_asset` | `AlpacaBarsConfigurationAsset` | `alpaca-connectors.AlpacaBarsConfigurationAsset` |
| `alpaca_connectors__asset_registration_operation` | `AssetRegistrationOperation` | `alpaca-connectors.AssetRegistrationOperation` |
| `alpaca_connectors__portfolio_rebalance_configuration` | `PortfolioRebalanceConfiguration` | `alpaca-connectors.PortfolioRebalanceConfiguration` |

The other four of the thirteen were the configuration tables of the product
[ADR 0027](../adrs/0027_one_portfolio_path.md) removed. Its revision `0027` copies the portfolio
configurations into `alpaca_connectors__portfolio_configuration`, which is registered in
`alpaca-connectors` from the start, and its revision `0028` drops the four. Every Environment whose
migrations run on MetaTables 0.1.66 or later has made the move: their finalization is refused until
it is done (below).

The "before" column is what the code declared; check it against the catalog before the move.
The portfolio code of earlier releases looked the two bars tables up with their physical name as
identifier, so their registered identifier may be `alpaca_connectors__bars_1d_sip_all` and
`alpaca_connectors__bars_1d_iex_raw` instead (not verified against a live catalog). The code of
this release finds the bars and interpolated-price tables by physical name only.
The only places the identifier is shown are the market-data dataset reads (`identifier`), which
read it from the catalog. The dataset `key` (`1d/sip/all`) and every uid are unchanged.

Who runs it: a person who holds `editor` or `owner` on `mainsequence.markets` (or on each of the
thirteen tables) and `editor` or `owner` on `alpaca-connectors`, or an Organization admin.
Adding needs `tables.update` on the table and `tables.create` on `alpaca-connectors`; removing
needs `tables.create` on `mainsequence.markets`.

It must run before the first migration on MetaTables 0.1.66 or later. That migration removes a
declared namespace the code no longer lists, the migration Job holds no role on
`mainsequence.markets`, and its finalization is refused until these tables have left it.

When: after step 1 has given the workloads their roles on `alpaca-connectors` (a moved table is
covered by the roles of its new namespace only; table-level roles move with the table), and
before the roles on `mainsequence.markets` are revoked (step 9). Runtime attachment and updater
binding resolve these tables by physical name, so both the previous and this release keep working
on either side of the move.

```python
from metatables import MetaTable

from alpaca_connectors.instances import project_instance_models
from alpaca_connectors.migrations import all_project_metatable_models

# The thirteen tables registered before this release; the instances of revision 0020 are
# registered in `alpaca-connectors` already.
instances = {model.__table__.name for model in project_instance_models()}
models = {
    model.__table__.name: model
    for model in all_project_metatable_models()
    if model.__table__.name not in instances
}
tables = MetaTable.filter_by_body(physical_table_name__in=sorted(models), limit=100)
assert len(tables) == len(models), "every project table must be registered exactly once"
before = {
    table.physical_table_name: (table.uid, [item.name for item in table.namespaces], table.identifier)
    for table in tables
}
print(before)
for table in tables:
    model = models[table.physical_table_name]
    table.add_to_namespace(model.__table__.info["namespace"])
    table.remove_from_namespace("mainsequence.markets")
    table.patch(identifier=model.__metatable_identifier__)
after = MetaTable.filter_by_body(physical_table_name__in=sorted(models), limit=100)
assert {table.physical_table_name: table.uid for table in after} == {
    name: uid for name, (uid, _, _) in before.items()
}
```

Run it from a checkout of this release against the
Environment's MetaTables (`mainsequence` session of the person above). Keep the printed `before`
mapping: adding each table back to its old namespace, removing it from `alpaca-connectors` and
patching its old identifier undoes the move.

## 8. Resume And Verify

Put the configurations held in step 2 back on the schedule, then check:

- the API's portfolio configuration detail (`observation_limit`) and weights routes show the
  history, read from Markets (ADR 0031);
- one publication of weights to a portfolio succeeds, and the next scheduled portfolio JobRuns
  succeed and publish their weights and values to Markets;
- the Markets portfolio values route returns the same rows as the API's detail;
- `alpaca-connectors account list` pages, searches and filters as before.

## Rolling Back

The move of step 6 can no longer be rolled back from this application: the `instances rollback`
command is gone with ADR 0031, and revision 0033 drops the three instances with their rows. The
previous release does not run on ms-markets 4.2.0.

## 9. Revoke The Markets Roles

The owner of `mainsequence.markets` revokes every role this application's workloads and Team
hold there: the namespace roles (`data_writer`, `data_viewer`, the migration Job's `editor`) and the
table `editor` grants on `AssetSnapshotsTS`, `AccountHoldingsTS`, `SignalWeightsTS`,
`InterpolatedPrices`, `PortfolioCalendarEventsTS`, `PortfolioRebalanceStateTS`,
`PortfolioWeightsTS` and `PortfoliosTS`. This application publishes to Markets' signal weights,
portfolio weights and values through the Markets API (ADR 0031), which needs none of them.

Once the release of ADR 0031 runs (step 5), the owner of `alpaca-connectors-markets` revokes the
Markets `data_viewer` on that namespace, and every other role on it, and removes the namespace
(ADR 0031, Rollout step 6). Nothing of this application reads or declares it any more.

## 10. Later

- A later revision drops the interpolated-price tables of revisions 0011
  (`ms_markets__interpolated_prices__t_*`) and 0021 (`alpaca_connectors__interpolated_prices__t_*`)
  once every Environment runs the release of
  [ADR 0029](../adrs/0029_interpolated_price_tables_are_named_like_every_project_table.md), whose
  revision 0034 replaced them (revisions add first and drop later).
- This application's rows written before ms-markets ADR 0047 stayed in the shared Markets time
  series. They belong to portfolios step 5 deletes, which keep them as their history; their
  removal is the Markets owners' decision.
