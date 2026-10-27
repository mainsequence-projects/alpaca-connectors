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
| Registration listing joined `AccountTable` | pages `alpaca_connectors__acct_alpaca` (revision 0019 adds the account name and active flag) and reads the page's Accounts in one batch |
| Universe listing and detail joined `AssetCategoryTable` and counted memberships | own universes plus `AssetCategory`/`AssetCategoryMembership` batched reads |
| Signal weights, portfolio values, weights, rebalance state, calendar events, orders, trades in `ms_markets__*` | `alpaca_connectors__<contract>__main` instances (revision 0020) |
| Interpolated prices in `ms_markets__interpolated_prices__t_*` (Markets namespace) | `alpaca_connectors__interpolated_prices__t_*` (revision 0021) |
| Portfolio reads of Markets tables only | each portfolio's Markets dataset catalog row points at the instances (`PortfolioDatasets.register` after every run; `register_signal` after every signal run) |
| Job schema gate read the ms-markets Alembic version table | waits until the Markets API's `/health` reports at least the ms-markets release of the image |
| 13 project tables in `mainsequence.markets` | in `alpaca-connectors` (step 7) |

Identities do not change: the uid of every table, the physical names of the project tables, every
`update_hash` (output tables and catalog namespaces do not enter it, and no hash namespace is
passed) and every signal uid. Catalog identifiers of moved tables gain the `alpaca-connectors.`
prefix (step 7).

## 0. Prerequisites On The Markets Side

These belong to the Markets deployment's owners, not to this repository:

- ms-markets 3.0.0 is released, and the Environment's Markets deployment has applied its revision
  `0022` and deployed the `api` release that serves `/api/v1/rows/` and the portfolio dataset
  catalog routes.

## 1. Roles On `alpaca-connectors`

The owner of `alpaca-connectors` (a person) grants, before the push and before the move, so each
workload can use the project tables once they leave `mainsequence.markets`:

| Workload | Role on `alpaca-connectors` | Why |
|---|---|---|
| `alpaca-connectors-migrate` | `editor` (holds it already) | registers and changes the project tables |
| `alpaca` (API) | `editor` | writes project rows; runs the signal (Universe run) and the orders and trades updaters in-process |
| `alpaca-etf-signal` | `editor` | writes Universe and asset-detail rows; runs the signal updater |
| `alpaca-etf-portfolio` | `editor` | runs the interpolation, calendar-event, rebalance, weights and values updaters |
| `alpaca-bars-update`, `daily-stock-bars-holdings-ivv` | `data_viewer`, plus their table `editor` on the two bars tables (table roles move with the tables) | read configurations; run the bars updaters |
| `alpaca-etf-signal-dispatcher`, `alpaca-etf-portfolio-dispatcher` | `data_viewer` | preflight reads |
| `alpaca-credential-migration` (one-off) | `data_writer` | reads and seals the account keys |

No workload needs a role on `mainsequence.markets` once step 9 is done.

## 2. Hold The Scheduled Runs

A signal run before step 6 publishes into an empty signal instance, and a portfolio run would
then start from that observation. Either push right after the 02:00 UTC portfolio dispatch and
finish step 6 before the 00:30 UTC signal dispatch, or take every configuration off the schedule
first (`alpaca-connectors signal update <uid> --no-scheduled`,
`alpaca-connectors portfolio update <uid> --no-scheduled`) and put it back in step 8. Until the
backfill, portfolio preflight blocks a run whose signal instance has no observation. Keep the runs
held while a move or a rollback runs.

## 3. Push This Release

The deployment workflow's migration Job applies revisions `0019` (account listing columns),
`0020` (the time-series instances, registered in `alpaca-connectors`) and `0021` (the namespaced
interpolated-price tables); the API and the Agent deploy after it; the Jobs take the new image and
their schema gate waits until the Markets API reports ms-markets 3.0.0 or later. The workflow
grants every resource that calls Markets `reader` on the `MainSequenceMarkets` branch.

## 4. Register The Namespaced Interpolated-Price Tables

As before for revision 0011, a person registers the dynamic interpolated-price tables with the
documented wrapper and checks them:

```bash
alpaca-connectors portfolio prepare-interpolated-prices
alpaca-connectors portfolio prepare-interpolated-prices --check-only
```

Revision 0021 already creates the tables of the two bars profiles, so no revision is generated.

## 5. Let Markets Read The Instances

The owner of `alpaca-connectors` grants the Markets workload `data_viewer` on exactly
`alpaca_connectors__portfoliosts__main`, `alpaca_connectors__portfolioweightsts__main` and
`alpaca_connectors__signalweightsts__main`, which the dataset catalog verifies and reconciles
(ms-markets ADR 0047, decision 5). Never grant it a namespace role here, and never a role on
`alpaca_connectors__acct_alpaca`: that table holds the application-encrypted account keys and is
not `include`d by Markets.

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
| `SignalSeriesMove` | signal weights of the signal configurations no portfolio points to | `signal_uid` |
| `PortfolioSeriesMove`, one per `ALPACA_ETF_PORTFOLIO__*` portfolio | its current signal's weights, rebalance state, weights and values | `signal_uid`, `portfolio_identifier` |
| `TableSeriesMove` | calendar events of the rebalance calendars | `calendar_identifier` |
| `TableSeriesMove` | orders and trades of the registered accounts | `account_identifier` |
| `TableSeriesMove`, one per bars profile | the revision-0011 interpolated prices | `asset_identifier` |

Signal observations are live ETF extractions, so they are copied, never recomputed. Each copy
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
creates are registered there directly. Existing tables are moved once with the MetaTables table
patch (MetaTables 0.1.56 or later), which reassigns the namespace and identifier of a registered
table:

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
| `alpaca_connectors__universe_source` | `UniverseSource` | `alpaca-connectors.UniverseSource` |
| `alpaca_connectors__asset_universe` | `AssetUniverse` | `alpaca-connectors.AssetUniverse` |
| `alpaca_connectors__bars_configuration` | `AlpacaBarsConfiguration` | `alpaca-connectors.AlpacaBarsConfiguration` |
| `alpaca_connectors__bars_configuration_asset` | `AlpacaBarsConfigurationAsset` | `alpaca-connectors.AlpacaBarsConfigurationAsset` |
| `alpaca_connectors__asset_registration_operation` | `AssetRegistrationOperation` | `alpaca-connectors.AssetRegistrationOperation` |
| `alpaca_connectors__etf_signal_job_configuration` | `AlpacaETFSignalJobConfiguration` | `alpaca-connectors.AlpacaETFSignalJobConfiguration` |
| `alpaca_connectors__portfolio_rebalance_configuration` | `PortfolioRebalanceConfiguration` | `alpaca-connectors.PortfolioRebalanceConfiguration` |
| `alpaca_connectors__etf_portfolio_configuration` | `AlpacaETFPortfolioConfiguration` | `alpaca-connectors.AlpacaETFPortfolioConfiguration` |

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
thirteen tables) and `editor` or `owner` on `alpaca-connectors`, or an Organization admin. The
table patch refuses a caller without `tables.update` on the table or `tables.create` on the
destination namespace.

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
before = {table.physical_table_name: (table.uid, table.namespace, table.identifier) for table in tables}
print(before)
for table in tables:
    model = models[table.physical_table_name]
    table.patch(namespace=model.__table__.info["namespace"], identifier=model.__metatable_identifier__)
after = MetaTable.filter_by_body(physical_table_name__in=sorted(models), limit=100)
assert {table.physical_table_name: table.uid for table in after} == {
    name: uid for name, (uid, _, _) in before.items()
}
```

Run it with `MSM_AUTO_REGISTER_NAMESPACE` unset, from a checkout of this release, against the
Environment's MetaTables (`mainsequence` session of the person above). Keep the printed `before`
mapping: patching each table back with its old namespace and identifier undoes the move.

## 8. Resume And Verify

Put the configurations held in step 2 back on the schedule, then check:

- the API's portfolio configuration detail (`observation_limit`) and signal observations routes
  show the copied history;
- `PortfolioDatasets.get(portfolio_uids=[...])` (Markets API) reports `populated` for every
  portfolio, and the Markets portfolio values route returns the same rows as before;
- the next scheduled signal and portfolio JobRuns succeed and append to the instances;
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
(`unregister_signal`: a portfolio its current signal, the signal move the others);
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
