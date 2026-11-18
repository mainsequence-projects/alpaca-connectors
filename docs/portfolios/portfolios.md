# Portfolios

## Goal

Run a portfolio of Alpaca assets whose weights an application provides: the application chooses
the weights, and the connector stores them, values the portfolio on its own Alpaca bars, keeps its
series and lets the account sync fund it. It is the connector's only kind of portfolio
([ADR 0024](../adrs/0024_portfolios_from_provided_weights.md),
[ADR 0027](../adrs/0027_one_portfolio_path.md)). Hunch builds every take this way.

Every portfolio belongs to one Alpaca account and follows one of that account's signal
definitions ([ADR 0028](../adrs/0028_every_portfolio_belongs_to_one_account.md)). Only the account's
owner creates them, and deleting the account deletes them.

The owner application:

1. registers the assets it needs through the asset routes
   (`POST /v1/assets/registration/execute`), so each has an `ALPACA::<uuid>` identifier and its
   trading calendar;
2. creates a signal definition on its account with its own key, and gives it weights;
3. creates portfolios of the account that follow the signal, as many as it wants;
4. gives the signal new weights whenever it chooses them, and deletes the portfolios and the
   signal when it is done.

Everything else is the connector's: the bars, the daily run, the valuation and the account sync.

The implementation lives in:

- `src/alpaca_connectors/portfolios/signal_definitions.py`: an account's signals, their identity
  and their deletion
- `src/alpaca_connectors/portfolios/provided_weights.py`: the observations, publishing, the
  creates, ending a portfolio and the owner check
- `src/alpaca_connectors/portfolios/adoption.py`: the `portfolio adopt` rollout
- `src/alpaca_connectors/portfolios/configurations.py`: the portfolio and rebalance configuration
  tables
- `src/alpaca_connectors/portfolios/execution.py`: resolution, preflight and the portfolio graph
- `src/alpaca_connectors/portfolios/signal_assets.py` and `bars_scope.py`: the assets a
  portfolio ever held, and the bars they need
- `src/alpaca_connectors/operations/configuration_jobs.py`: runs on the workflow-declared Job
- `src/jobs/run_alpaca_portfolio.py` and `src/jobs/dispatch_alpaca_portfolio.py`: the Job and its
  daily dispatcher
- `src/alpaca_connectors/api/app/routers/portfolio_configurations.py`: the HTTP surface
- `src/alpaca_connectors/cli/portfolios.py`: the CLI

## The Standard Endpoints

These are the endpoints a venue connector serves for a portfolio an application owns. Hunch asks
every venue's connector to serve the same ones (Hunch ADR 0030, decision 7).

| Route | Effect | Who |
| --- | --- | --- |
| `POST /v1/signal-definitions` | create a signal of the account, with the application's key | the account's owner |
| `POST /v1/signal-definitions/{uid}/weights` | give the signal new weights: one or more observations | the account's owner |
| `GET /v1/signal-definitions/{uid}/weights` | its latest observations: their times and each asset's weight at each time | the account's owner, or any Main Sequence caller |
| `PATCH`, `DELETE /v1/signal-definitions/{uid}` | rename it; delete it once no portfolio follows it | the account's owner |
| `POST /v1/portfolio-configurations` | create a portfolio of the account that follows one of its signals | the account's owner, who becomes its owner |
| `POST /v1/portfolio-configurations/provided-weights` | ADR 0024's create, with its first observations; Hunch uses it | any caller, who becomes its owner; an inline feed needs an account the caller may configure |
| `POST`, `GET /v1/portfolio-configurations/{uid}/weights` | ADR 0024's weights routes, on the signal the portfolio follows | its owner; reading, anyone |
| `PATCH /v1/portfolio-configurations/{uid}` | change its settings; pause or resume it with `scheduled` | its owner |
| `DELETE /v1/portfolio-configurations/{uid}` | retire its `Portfolio`, then delete it ([Ending A Portfolio](#ending-a-portfolio)) | its owner |
| `POST /v1/portfolio-configurations/{uid}/actions/run` | run it now on the portfolio Job | anyone |
| `GET /v1/portfolio-configurations`, `GET .../{uid}`, `GET .../{uid}/runs`, `GET .../discovery` | list, detail, its runs, the discovery contract | anyone |

**A signal definition** takes `account_uid`, the application's `key` (unique within the account),
`name` and `description?`. Its signal is ms-markets' `ApplicationWeights` keyed by the account UID
and that key, so two accounts never share a signal. It answers its `signal_uid` and the number of
portfolios that follow it. Its observations, signal-asset rows and Markets `SignalMetadata` go
with it, unless a retired `Portfolio` still follows the signal.

**Create** (`POST /v1/portfolio-configurations`) takes `name`, `description?`, `account_uid`,
`signal_definition_uid` (a definition of the same account; the database refuses another
account's), `rebalance_configuration_uid`, the bar profile of its own bars configuration under
`bars`, the valuation and fee settings (`valuation_column`,
`valuation_maximum_staleness_seconds`, `fail_on_missing_prices`, `commission_fee`) and
`scheduled?`. It stores the row, creates its own bars configuration and the `Portfolio` row, in
that order, and a refused create leaves no row. It answers the configuration, the `Portfolio`
(`portfolio_uid`, and `portfolio_unique_identifier` `ALPACA_PORTFOLIO__<uid hex>`) and its bars
configuration.

**ADR 0024's create** is supported with the rules it had before ADR 0028; Hunch creates its takes
this way. It takes either `bars_configuration_uid` (a feed of the account) or `bars_configuration`
(`name`, `description?`, `account_uid`, `frequency_id`, `feed`, `adjustment`, `scheduled?`), the
rebalance, valuation and fee settings and `observations`. The caller owns the portfolio. A named
feed is not checked; an inline one needs an account the caller may configure: another owner's
account is refused, and one without an owner stays open (ADR 0022). The portfolio belongs to that
account, gets its own bars configuration with that profile, and a signal definition of its own
whose key is its configuration UID, as its signal's key was; the observations are published to it.

**Not adopted yet.** A portfolio created before ADR 0028 that revision 0029 could not give an
account (its bars ran on an account without an owner, or on another owner's) answers
`adopted: false` and works exactly as before: it takes weights on its own signal, keyed by its
configuration UID, runs on the `portfolios` feed it names, and that feed keeps pricing it.
`alpaca-connectors portfolio adopt --execute` adopts the ones whose owner later owns the account
and leaves the rest as they are; nothing ends them.

## Observations

An observation is `{observed_at, weights: {<ALPACA::uuid>: <weight>}}`, and `observed_at` needs a
timezone:

- **One observation is the whole set.** An asset the previous observation held and this one leaves
  out is written at `0`.
- **Weights are stored as given.** No normalisation: whether they sum to one, or may be negative,
  is the application's rule. The account sync sizes them as they are.
- **A history is only extended.** Several observations in one call start or extend it. An
  observation at or before the last stored one is refused (`409 observation_not_after_last`),
  because portfolios that rebalanced on a published observation keep it. A portfolio whose history
  was wrong is deleted and created again.
- **The signal.** The observations are an ms-markets `ApplicationWeights` signal whose
  configuration is this connector's namespace and the portfolio configuration uid, so its
  `signal_uid` never changes. They are written to this application's signal weights instance
  (`alpaca_connectors__signalweightsts__main`), and the assets each one names to the signal-asset
  table (`alpaca_connectors__signal_asset`) first.
- **The assets.** Each must be a registered Alpaca asset: an unknown one is
  `422 unknown_asset`, and one without Alpaca details, for which no bars can be fetched, is
  `422 asset_not_alpaca`.

## Ownership

- **The acting owner at creation owns the portfolio.** That is the person pair
  `(owner_gateway_uid, owner_user_uid)` of [ADR 0019](../adrs/0019_account_owners_vouched_for_by_trusted_applications.md),
  stored on the row.
- **Only the owner** gives it weights, changes, pauses, resumes or deletes it; anyone else gets
  `403 portfolio_owned_by_another`. The Agent acts for its requester, so it reaches only its
  person's portfolios.
- **Reads and runs are open** to every verified caller. Responses never carry the owner pair.

## Bars

Each portfolio has its own bars configuration (ADR 0028, decision 4): it names the portfolio,
runs with the portfolio's account's keys, has the `portfolios` source and is deleted with the
portfolio. Its profile (`1d/sip/all` or `1d/iex/raw`) chooses the shared price table its bars land
in, whose rows name no account ([Alpaca Bars](../market_data/alpaca_bars.md)):

- **Its assets** are every asset the portfolio's signal ever published, read from the
  signal-asset table, never from the signal weights series. A portfolio whose `Portfolio` is
  retired prices nothing; a paused one still counts. The `resolve` action
  (`POST /v1/market-data/bar-configurations/{uid}/actions/resolve`) shows the list a run would use.
- **New weights need no edit.** The next bars run picks up an asset the signal was just given.
- **Shared prices.** Two accounts' configurations on the same profile write the same table; a
  portfolio is valued from it whichever account fetched a price.
- **The order of the day.** Bars run at 00:00 UTC and portfolios at 02:00 UTC, so weights given
  during a day get their bars before the next scheduled run. Run the bars update first when
  running a portfolio at once after giving it a new asset; otherwise the run fails on missing
  prices when `fail_on_missing_prices` is set.

## Rebalance Configurations

A rebalance configuration is a reusable calendar-event policy, shared by any number of portfolios
([ADR 0009](../adrs/0009_calendar_event_portfolio_timing.md)). The supported strategy is
`calendar_event_signal` (`CalendarEventSignal`). It stores the persisted calendar identifier
(`NYSE` by default), the session label, the event (`market_open` or `market_close`), an offset in
seconds, and the cadence (`every_session`, or `weekly` on one weekday).

At each eligible calendar event the portfolio rebalances to the latest observation at or before
that event, so weights given after a close are first used at the next configured event. Early
closes use their actual persisted close times; they are not normalised to midnight or a nominal
close.

```text
GET|POST         /v1/portfolio-rebalance-configurations
GET|PATCH|DELETE /v1/portfolio-rebalance-configurations/{uid}
GET              /v1/portfolio-rebalance-configurations/discovery
```

A rebalance configuration that a portfolio names cannot be deleted.

## Execution Graph

```text
Provided observations ──────────────> ApplicationWeights signal

Bars Configuration ─> raw Bars ─> persistent InterpolatedPrices

Rebalance Configuration ────────────> PortfolioCalendarEvents ─> CalendarEventSignal

ApplicationWeights + CalendarEventSignal ─> PortfolioRebalance
                                          └> PortfolioWeights

PortfolioWeights + InterpolatedPrices ──> PortfoliosUpdater ─> PortfoliosTS instance
```

- **Where the outputs live.** Every output is in this application's `alpaca-connectors` namespace
  (ms-markets ADR 0047): the signal weights, calendar events, rebalance state, portfolio weights
  and values in its contract instances (`alpaca_connectors__<contract>__main`,
  `alpaca_connectors.instances`), and the interpolated prices in
  `alpaca_connectors__interpolated_prices__t_<suffix>`. The instances are also in
  `alpaca-connectors-markets`, where Markets reads them
  ([ADR 0025](../adrs/0025_markets_reads_the_instances_through_a_second_namespace.md)).
- **The catalog.** After each run the portfolio's Markets dataset catalog row is registered and
  reconciled (`PortfolioDatasets.register`), so Markets reads the values, weights and signal from
  those tables. The `Portfolio` row, its calendar and the signal metadata are Markets registries,
  written through the Markets API.
- **The assets priced** are every asset the portfolio ever held, from the signal-asset table, so a
  history it started with is valued, including assets that history already dropped.
- **No producer runs as a dependency.** A run requires a published observation, then updates the
  interpolated prices, the calendar events, the rebalance decisions, the executed weights and the
  valuation with `update_tree=False`. The bars have their own Job and schedule.

## Valuation

- **Interpolated prices.** `InterpolatedPrices` gives a value at a real portfolio time, and
  `valuation_maximum_staleness_seconds` bounds how old that price may be.
- **Missing prices.** `fail_on_missing_prices` decides whether an incomplete valuation fails the
  run.
- **No invented times.** Neither setting invents daily times, extends the calculation to the
  current time or changes when weights are valid. `PortfoliosUpdater` writes only at valuation
  observations, and `PortfolioWeights` only at rebalance events.
- **Calendars.** Every asset's bars follow the trading calendar it is linked to
  ([ADR 0026](../adrs/0026_assets_are_registered_with_their_trading_calendar.md)), and the
  rebalance calendar drives the events and the `Portfolio` row.
- **What it does not model.** Partial fills, volume participation, market impact and slippage
  beyond the configured commission fee.

## The Portfolio Job

Every portfolio runs on the `alpaca-portfolio` Job ("Alpaca Portfolio") declared in
`.mainsequence/workflows/alpaca-connectors-api.yaml`, one configuration per run
(`--configuration-uid <uid>`). Its dispatcher, `alpaca-portfolio-dispatcher`, starts one run per
scheduled configuration at 02:00 UTC ([Jobs](../operations/jobs.md),
[ADR 0013](../adrs/0013_workflow_declared_configuration_jobs.md)).

- **The configuration holds no schedule or compute.** Its only operational field is `scheduled`,
  which decides whether the dispatcher includes it. Requests that send schedule or compute settings
  get `422`. `PATCH {"scheduled": false}` pauses a portfolio without validating its references.
- **The preflight.** A run needs its bars configuration to exist and be enabled, its rebalance
  configuration to exist with a supported strategy, and at least one published observation. The
  dispatcher skips a configuration whose preflight is blocked, and the run action refuses it.
- **A run** returns `202` with the JobRun UID and a `status_url`. It is refused while the
  configuration's newest run is pending or running, or while the Job's image is not ready.
- **Responses** report the Job as `job_uid` and `job` (its dispatcher's schedule, compute and
  image), both null until a push creates the Job.

## Ending A Portfolio

`DELETE /v1/portfolio-configurations/{uid}` ends a portfolio as ms-markets' portfolio lifecycle
defines it (ms-markets ADR 0049), and so does every other delete, the CLI's included
([ADR 0027](../adrs/0027_one_portfolio_path.md), decision 4):

1. **Retire the `Portfolio`** through `MarketsClient.retire_portfolio`. Markets refuses while an
   account's current target names the portfolio: the call answers
   `409 portfolio_in_account_target`, names no account, and changes nothing.
2. **Forget the signal's assets** in the signal-asset table.
3. **Delete the configuration row.**

The answer is `{configuration_uid, deleted: true, portfolio: {uid, unique_identifier, state,
retired_at}}`, with `state` `retired`, or `absent` for a configuration whose `Portfolio` no longer
exists. Retiring a retired portfolio does nothing, so a call that failed after the retirement is
safe to repeat.

Retiring deletes nothing. The `Portfolio`, its series, its catalog registration and every
account's history stay. A retired portfolio cannot be named by a new target, gets no virtual-fund
holdings, leaves discovery, and its updaters write nothing after `retired_at`.

## CLI

No CLI command creates a portfolio or gives it weights: an application does, over HTTP
([ADR 0024](../adrs/0024_portfolios_from_provided_weights.md), decision 7).

Create a reusable rebalance policy:

```bash
alpaca-connectors portfolio rebalance create \
  --name "NYSE close" \
  --strategy calendar_event_signal \
  --calendar-identifier NYSE \
  --session-label regular \
  --rebalance-event market_close \
  --event-offset-seconds 0 \
  --rebalance-cadence every_session
```

List, inspect, change, run and delete portfolios:

```bash
alpaca-connectors portfolio list
alpaca-connectors portfolio get <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio update <PORTFOLIO_CONFIGURATION_UID> --no-scheduled
alpaca-connectors portfolio run <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio runs <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio delete <PORTFOLIO_CONFIGURATION_UID>             # dry run
alpaca-connectors portfolio delete <PORTFOLIO_CONFIGURATION_UID> --execute   # retires its Portfolio first
```

Prepare and verify the persistent interpolation tables for every migrated Alpaca bars profile:

```bash
alpaca-connectors portfolio prepare-interpolated-prices
alpaca-connectors portfolio prepare-interpolated-prices --check-only
```

The preparation command resolves every registered Alpaca bars table in one catalog request,
derives the ms-markets dynamic storage identities, generates one Alembic revision when needed,
applies the project's migration history and verifies every resulting `TimeIndexMetaTable`. A
portfolio run only checks and attaches those tables; it never creates schema.

There is no Environment argument: the CodeRepositoryBranch runtime resolves it.

## The Detail And Its Statistics

`GET /v1/portfolio-configurations/{uid}` returns the configuration with its bars and rebalance
configurations, the Job's live settings, the canonical `Portfolio` (read through the Markets API)
and a `provided_weights` block: the signal UID, the first and last observation times and the asset
count. With `observation_limit` (default 2,500, at most 5,000) it also returns the portfolio's most
recent values from this application's `PortfoliosTS` instance
(`alpaca_connectors__portfoliosts__main`), in time order. The read is constrained by the
portfolio's unique identifier and runs only when a consumer opens that portfolio.

The same answer carries window-scoped statistics computed from those daily values with
`empyrical-reloaded`: total and annualised return, annualised volatility, Sharpe, Sortino,
maximum drawdown, Calmar, best and worst daily return, and the positive-period ratio, over 252
periods a year and a 0% risk-free rate. It reports the exact observation count and window, and
marks a window shorter than the stored history as truncated, so it is never read as
since-inception. Alpha and beta are absent: a portfolio has no benchmark.

## Required Platform State

Before a portfolio's first run in an Environment:

- the installed ms-markets release, 4.0.1 or later, is the one the Environment's Markets API runs
  (the Job's schema gate checks it); this application holds no role on `mainsequence.markets` and
  reaches the `Portfolio`, calendar and signal metadata through the Markets API;
- this project's revisions are applied by the migration Job, including the instances of revisions
  `0020` and `0021` and the portfolio configuration table of revision `0027`;
- the bars configuration is enabled, and its bars table holds the portfolio's assets;
- the configuration-derived `InterpolatedPrices` storage is migrated and registered
  (`prepare-interpolated-prices`);
- the declared `Alpaca Portfolio` Job exists on the branch, its image is ready, and its workload
  holds `editor` on `alpaca-connectors`
  ([table access](../operations/jobs.md#table-access-and-metatables-roles)).

## Verification

After a successful run, check that:

- the JobRun's `command_args` are `--configuration-uid <uid>` for the intended configuration;
- the persistent `InterpolatedPrices` table contains the portfolio's assets;
- the `PortfolioCalendarEventsTS` instance contains the configured session events, and the
  `PortfolioRebalanceStateTS` and `PortfolioWeightsTS` instances use those event times rather than
  dates normalised to midnight;
- the configuration stores the resulting `Portfolio.uid`;
- the `PortfolioWeightsTS` and `PortfoliosTS` instances contain output for that portfolio, and its
  Markets dataset catalog row is `populated`;
- the workflow declaration remains the only source of schedule, compute, image and automatic
  deployment.
