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
| Job schema gate read the ms-markets Alembic version table | waits until the Markets API's `/health` reports at least the ms-markets release of the image |
| 13 project tables in `mainsequence.markets` | in `alpaca-connectors` (step 7) |

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

The owner of `alpaca-connectors` (a person) grants, before the push and before the move, so each
workload can use the project tables once they leave `mainsequence.markets`:

| Workload | Role on `alpaca-connectors` | Why |
|---|---|---|
| `alpaca-connectors-migrate` | `editor` (holds it already) | registers and changes the project tables |
| `alpaca` (API) | `editor` | writes project rows; publishes provided weights; runs the orders and trades updaters in-process |
| `alpaca-portfolio` | `editor` | runs its bars configuration's update (issue #39), then the interpolation, calendar-event, rebalance, weights and values updaters |
| `alpaca-bars-update` | `data_viewer`, plus its table `editor` on the two bars tables (table roles move with the tables) | read configurations; run the bars updater |
| `alpaca-portfolio-dispatcher` | `data_viewer` | preflight reads |
| `alpaca-bars-update-dispatcher` | `data_viewer` | read the enabled, scheduled bar configurations (ADR 0021) |

No workload needs a role on `mainsequence.markets` once step 9 is done.

The owner also creates the namespace `alpaca-connectors-markets`
([ADR 0025](../adrs/0025_markets_reads_the_instances_through_a_second_namespace.md)) and grants
`alpaca-connectors-migrate` `editor` on it, directly or through its Team (step 5 has the
command): the migration adds every Markets contract instance
to it, which needs `tables.create` there. The person who runs step 4 needs the same role. No
other workload of this application needs a role on it, since each already reaches the instances
through `alpaca-connectors`.

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

## 4. Register The Namespaced Interpolated-Price Tables

[ADR 0029](../adrs/0029_interpolated_price_tables_are_named_like_every_project_table.md)
(proposed, waiting for MainSequenceMarkets#95) removes this step: the tables are named after their
bars profile and the migration Job creates them. Until then, as before for revision 0011, a person
registers the dynamic interpolated-price tables with the documented wrapper and checks them:

```bash
alpaca-connectors portfolio prepare-interpolated-prices
alpaca-connectors portfolio prepare-interpolated-prices --check-only
```

Revision 0021 already creates the tables of the two bars profiles, so no revision is generated.

## 5. Let Markets Read The Instances

Every Markets contract instance of this application (signal weights, portfolios, portfolio
weights, rebalance state, calendar events, orders, trades and the interpolated prices) is in two
namespaces: `alpaca-connectors` and `alpaca-connectors-markets`
([ADR 0025](../adrs/0025_markets_reads_the_instances_through_a_second_namespace.md), MetaTables
0.1.66 or later). The code declares the second one, and the next `upgrade head` adds the
memberships with no revision.

The owner of `alpaca-connectors-markets`, or an Organization admin, grants the Markets workload
`data_viewer` on that namespace, once per Environment:

```bash
alpaca-connectors instances grant-markets-access --markets-workload-uid <uid>             # dry run
alpaca-connectors instances grant-markets-access --markets-workload-uid <uid> --execute
```

The command creates the namespace when it is missing, so the person who runs it owns it. With
`--editor-team-uid <uid>` it also grants that Team `editor` there, which the migration Job needs
to add the instances; run it before the push. Its report lists the instances that are not in the
namespace yet. The dataset catalog verifies and reads the instances with it
(ms-markets ADR 0047, decision 5), and an instance added later is covered. Never grant the
Markets workload a role on `alpaca-connectors`, and never one on `alpaca_connectors__acct_alpaca`:
that table holds the application-encrypted account keys and is not `include`d by Markets.

Check it as the owner, after the migration Job has run:

```python
from metatables import TimeIndexMetaTable

from alpaca_connectors.instances import project_instance_models

names = sorted(model.__table__.name for model in project_instance_models())
for table in TimeIndexMetaTable.filter_by_body(physical_table_name__in=names, limit=100):
    print(table.physical_table_name, sorted(item.name for item in table.namespaces))
```

The migration Job checks it on every deploy (issue #37). Its last log line,
`{"markets_access": {...}}`, lists under `instances_missing_from_namespace` each instance that is not
in `alpaca-connectors-markets` yet, so the gap shows at deploy and not at the first call that needs
it. The report never fails the deploy. A call that meets the gap answers
`503 signal_not_registered` and stores nothing.

## 6. Move The History

As a person who holds `data_viewer` on the source tables in `mainsequence.markets` and
`data_writer` on `alpaca-connectors`, with the scheduled runs held (step 2):

```bash
alpaca-connectors account backfill-listing-fields             # dry run
alpaca-connectors account backfill-listing-fields --execute
alpaca-connectors instances backfill                          # dry run: rows per key
alpaca-connectors instances backfill --execute --register-catalog
alpaca-connectors instances backfill                          # every key: missing_rows == 0
```

`backfill-listing-fields` copies each registration's account name and active flag (read through
the Markets API) onto its details row.

`instances backfill` runs ms-markets' shared series moves (`msm.storage_copies`,
`msm_portfolios.services.series_moves`) over this application's history:

| Move | Rows | Key |
|---|---|---|
| `PortfolioSeriesMove`, one per portfolio configuration's `Portfolio` | its current signal's weights, rebalance state, weights and values | `signal_uid`, `portfolio_identifier` |
| `TableSeriesMove` | calendar events of the rebalance calendars | `calendar_identifier` |
| `TableSeriesMove` | orders and trades of the registered accounts | `account_identifier` |
| `TableSeriesMove`, one per bars profile | the revision-0011 interpolated prices | `asset_identifier` |

Since [ADR 0027](../adrs/0027_one_portfolio_path.md) the command no longer moves the signals
of the signal configurations that ADR removed. An Environment that has not run the backfill runs it
before that ADR's first release, whose
[inventory](../adrs/0027_one_portfolio_path.md#before-r1-what-each-environment-holds) checks it.

Signal observations are published, never restated, so they are copied, never recomputed. Each copy
writes only the rows the instance lacks (by the full index) through the ms-markets
`TimeIndexRowsCopy` updater, then counts both sides per key. A move whose keys are all complete is
`moved`; with `--register-catalog` it then binds its signals (`register_signal`) and writes its
portfolio's catalog row last (`register`), which needs step 5; before any catalog call it
refuses a move whose signals have no `SignalMetadata` row. A key still missing rows leaves the move
`incomplete` and changes nothing in Markets; rerunning copies only the gaps. `--batch-size N` sets
how many keys one copy request reads (ms-markets `keys_per_read`). Calendar events
are copied although the engine could rebuild them from the persisted calendar. The selection of
what moves comes from this application's own tables; the command reads no Markets table itself.

The same moves run from the ms-markets CLI, which registers the catalog:

```bash
msm series move alpaca_connectors.operations.instance_backfill:series_moves            # dry run
msm series move alpaca_connectors.operations.instance_backfill:series_moves --execute
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

`MSM_AUTO_REGISTER_NAMESPACE`, when it is set, silently replaces the declared namespace (and
suffixes physical names). No hosted resource sets it; never set it outside tests and local
examples.

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

Run it with `MSM_AUTO_REGISTER_NAMESPACE` unset, from a checkout of this release, against the
Environment's MetaTables (`mainsequence` session of the person above). Keep the printed `before`
mapping: adding each table back to its old namespace, removing it from `alpaca-connectors` and
patching its old identifier undoes the move.

## 8. Resume And Verify

Put the configurations held in step 2 back on the schedule, then check:

- the API's portfolio configuration detail (`observation_limit`) and weights routes show the
  copied history;
- `PortfolioDatasets.get(portfolio_uids=[...])` (Markets API) reports `populated` for every
  portfolio, and the Markets portfolio values route returns the same rows as before;
- the next scheduled portfolio JobRuns succeed and append to the instances;
- `alpaca-connectors account list` pages, searches and filters as before.

## Rolling Back

Until step 9 revokes the roles on `mainsequence.markets`, the move can be undone. With the
scheduled runs held, as a person with write access on the shared Markets tables:

```bash
alpaca-connectors instances rollback                  # dry run
alpaca-connectors instances rollback --execute        # or: msm series rollback <same callable> --execute
```

The rollback runs the same moves backwards (portfolios first): it copies the rows the instances
gained since the move into the shared tables, verifies every key, then removes each portfolio's
catalog row (`unregister`, keeping signal bindings) and unbinds only the signals each move moved
(`unregister_signal`: each portfolio its current signal);
`--keep-catalog` leaves them. `msm series rollback` passes `rollback=True` to the callable, which
returns the same moves. The instances keep their rows. This release always writes the instances and
registers the catalog after every run, so deploy the previous release before resuming the runs.

## 9. Revoke The Markets Roles

The owner of `mainsequence.markets` revokes every role this application's workloads and Team
hold there: the namespace roles (`data_writer`, `data_viewer`, the migration Job's `editor`) and the
table `editor` grants on `AssetSnapshotsTS`, `AccountHoldingsTS`, `SignalWeightsTS`,
`InterpolatedPrices`, `PortfolioCalendarEventsTS`, `PortfolioRebalanceStateTS`,
`PortfolioWeightsTS` and `PortfoliosTS`.

## 10. Later

- A later revision drops the revision-0011 tables `ms_markets__interpolated_prices__t_*` once
  every Environment has copied them (revisions add first and drop later).
- This application's earlier rows stay in the shared Markets time series; Markets has no route
  to delete a provider's portfolio values short of deleting the portfolio. Their removal is the
  Markets owners' decision.
