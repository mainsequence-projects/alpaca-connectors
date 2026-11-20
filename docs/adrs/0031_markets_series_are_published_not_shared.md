# ADR 0031: Markets Series Are Published, Not Shared

## Status

Accepted on 2026-11-19 by the owner. On 2026-11-20 the owner decided that it ships as one
release, with no intermediate steps (see [Rollout](#rollout)). Implemented on the branch
`adr-0031-publish-series` (2026-11-20) against ms-markets 4.2.0, the release that implements
ms-markets ADR 0060 ("Producers Publish, Markets Reads Only Its Own Tables"), which this ADR
applies to this application. The same release adapts this application to ms-markets 4.1.0's
rule that a trade happens at the next observed price (ms-markets ADR 0058, decided by the owner
for this application on 2026-11-20; decision 5). The portfolios that exist before it are deleted
(owner, 2026-11-20; [Rollout](#rollout)). It runs on ms-markets 4.2.0 as published.

When implemented it withdraws
[ADR 0025](0025_markets_reads_the_instances_through_a_second_namespace.md), removes
`alpaca-connectors-markets` from decisions 1, 2 and 6 of
[ADR 0030](0030_workloads_get_their_roles_through_their_branch.md), and changes where
[ADR 0024](0024_portfolios_from_provided_weights.md) and
[ADR 0028](0028_every_portfolio_belongs_to_one_account.md) keep signal weights and portfolio series. No
business rule of those ADRs changes.

## Context

### What this application does today

It computes portfolios from weights that people publish, and keeps the results in seven
instances of Markets contracts in its own namespace (`alpaca_connectors.instances`):

| Instance | Written by | Read by this application |
|---|---|---|
| Signal weights | `ApplicationWeights.publish`, from the weights routes | Run preflight, the publish guard, key reuse guard, `GET .../weights`, portfolio detail |
| Portfolio values | `PortfoliosUpdater`, in a portfolio run | `GET /v1/portfolio-configurations/{uid}` value history and performance |
| Portfolio weights | `PortfolioWeights`, in a portfolio run | `size_account_target` in the account sync and after fills |
| Rebalance state | `PortfolioRebalance`, in a portfolio run | Nothing here; the engine continues from it |
| Calendar events | `PortfolioCalendarEvents`, for `calendar_event_signal` | Nothing here; the engine reads it |
| Orders, trades | The execution ledger | Nothing; order listing reads `OrderManager.metadata_json` |

Interpolated prices are in two more tables of this namespace.

So that the Markets API can serve the first three, this application also:

- registers them in the Markets dataset catalog (`PortfolioDatasets.register` after every run,
  `register_signal` before every publication of weights);
- declares all seven in a second namespace, `alpaca-connectors-markets` (ADR 0025);
- has a person grant the `MainSequenceMarkets` branch `data_viewer` on it in each Environment
  (`alpaca-connectors instances grant-markets-access`);
- prints a `markets_access` report from the migration Job on every deploy.

### What is wrong with it

- The Markets API reads this application's tables. Markets is the base this application builds
  on, and it should know nothing about us.
- The grant is a person's step in every Environment. Production missed it once, and the first
  provided-weights registration failed (issue #37). A missing grant still answers
  `503 signal_not_registered` and stores nothing.
- Anyone who may call the Markets read routes reads these tables through Markets' grant.
- This application wrote routines every producer needs: the publish guard, signal and portfolio
  history reads, the ordered run, the ordered deletes and the history move.

ms-markets ADR 0060 fixes the direction: a producer publishes signal weights, portfolio weights
and portfolio values to Markets, which stores them once in its own tables, and Markets reads no
table of another application. It ships the series operations and the recipes a producer reuses.

## Decision

1. **This application has no table for signal weights, portfolio weights or portfolio values.**
   It publishes them to Markets and reads them back from Markets. The instances
   `AlpacaSignalWeightsStorage`, `AlpacaPortfolioWeightsStorage` and `AlpacaPortfoliosStorage`
   are dropped by revision 0033 of the same release, with their rows: the portfolios that exist
   before it are deleted, not carried across (Rollout).
2. **Its private tables stay in `alpaca-connectors`, and Markets never reads them:** rebalance
   state, calendar events, interpolated prices, bars, orders and trades, and every table of its
   own. Their `RESTRICT` keys to Markets registries stay.
3. **Nothing exists for Markets to read this application.** Removed:
   `MARKETS_INSTANCES_NAMESPACE` and its `BRANCH_ACCESS` entry, `share_with_markets`,
   `operations/markets_access.py`, `instances grant-markets-access`, the migration Job's
   `markets_access` report, `portfolios/datasets.py` and its two call sites. A new Environment
   needs no access step for Markets beyond `reader` on the `MainSequenceMarkets` branch, which
   the workflow already declares.
4. **Weights are published with the ms-markets recipe `publish_signal_weights`.** The weights
   routes, their owner check and their answers do not change. This application keeps what is its
   own and runs it before the recipe:
   - `require_portfolio_owner` (403 `portfolio_owned_by_another`);
   - `require_alpaca_assets` (422 `unknown_asset`, `asset_not_alpaca`);
   - the error codes it answers with: `empty_observations`, `invalid_observation`, and 409
     `observation_not_after_last`, mapped from the recipe's refusals.

   The signal's metadata on first publication and writing a dropped asset at 0 are the recipe's.
   The not-after-last rule is checked here first as well (see Implementation Notes).
   `signal_not_registered` no longer exists.
5. **A portfolio run uses the ms-markets recipe `run_portfolio`.** The triggers do not change:
   the Job, the 02:00 UTC dispatcher, `POST .../actions/run`, the first run after a create, and
   the Agent. This application still supplies and decides:
   - the bars update before the run, whose failure does not stop it;
   - interpolated prices from its own bars, as the valuation source;
   - calendar events for both strategies: the calendar's `market_open` and `market_close`, in its
     private calendar-events table, which `immediate_signal` and `calendar_event_signal` read (the
     latter's configured event is one of them);
   - the rebalance configuration, the calendar and the signal of the configuration;
   - the portfolio's currency, US dollars, the Alpaca account's cash (ms-markets ADR 0059);
   - the `ALPACA_PORTFOLIO__<hex>` identifier, and storing the `portfolio_uid` on the
     configuration;
   - the warning when no first rebalance has happened yet (issue #41).

   The run starts at the signal's first observation, is scoped to its one signal, and creates or
   updates the `Portfolio`, as today. It ends by publishing; nothing is registered or reconciled.

   Trades happen at the next observed price, as ms-markets ADR 0058 defines for every strategy
   (owner, 2026-11-20). The strategy's moment is the signal's time for `immediate_signal` and the
   configured event for `calendar_event_signal`; the earliest moment is that moment while the
   calendar's market is open (`market_open <= t <= market_close`), otherwise the next open; each
   asset then trades at its first price observed at or after it, strictly after the signal that
   set its target, inside a session (`market_open < t <= market_close`), never at an
   interpolated price. This application's prices are daily Alpaca bars stamped at the session's
   close, so every trade happens at a session's close. The execution choices are ms-markets'
   defaults (`ExecutionPolicy`: shares sized at the decision, no negative cash, sells before
   buys, pro-rata allocation of too little cash, an uncovered buy cancelled), and between
   rebalances the weights drift with prices (ms-markets ADR 0059). The first-rebalance warning
   computes its time by the same rule.

   What changes for a portfolio's results: an `immediate_signal` portfolio no longer trades at
   the moment its weights are published but at the next session close after them (the same
   session's close during a session; Monday's close for weights given on a weekend). A
   `calendar_event_signal` portfolio on `market_close` trades at the same close as before; on
   `market_open` it trades at that session's close instead of its open. Both hold shares and cash
   between rebalances, so their weights drift with prices instead of returning to target at every
   price, and a buy that cash cannot cover is cut. A portfolio that exists before this release is
   deleted (Rollout): left in place, its run would recompute its rebalance state under the new
   rule next to the rows of the earlier one, and its weights would jump from one to the other at
   its next trade.
6. **Reads of the three series come from Markets, through its recipes.** Each business use stays:

   | Use | Today | After |
   |---|---|---|
   | Run preflight: "Signal … has no published observations" | `read_signal_observation_bounds_by_signal` | `signal_history` |
   | `signal_start_time`, the run's start | Same read | `signal_history` |
   | Publish guard, first-publication check | `read_signal_last_observation_at` | `signal_history`, then inside `publish_signal_weights` |
   | Key reuse guard, 409 `signal_key_taken` | `read_signal_observation_bounds` | `signal_history` |
   | `portfolio_weights_missing` message (issue #41) | `read_signal_observation_bounds` | `signal_history` |
   | `GET .../weights` (limit 100 and 500), detail's `provided_weights` | `read_signal_observation_matrix` | `signal_history`, joined here to the Alpaca symbol and name |
   | Value history and performance, 2,500 by default and 5,000 at most | `read_portfolio_history` | `portfolio_history`; the performance figures stay here |
   | `size_account_target` in the sync and after fills | `read_portfolio_weights` | `weights_as_of`; `portfolio_weights_missing` stays |

   `portfolios/signal_history.py`, `read_portfolio_history`'s SQL and `read_portfolio_weights`'s
   SQL are removed.
7. **Deletes keep their order and their rules; fewer steps are this application's.**
   - Ending a portfolio still retires its `Portfolio` first and records it in `EndedPortfolio`.
     Its series, its signal's rows and account history stay, now in Markets.
   - `delete_signals_rows` still keeps a signal that a retired portfolio follows. For the rest
     it forgets the signal's assets and calls `end_signal`; Markets deletes the weights and the
     metadata, and still refuses while a `Portfolio` follows the signal.
   - The account delete keeps its steps and their order. Step 6 deletes this application's
     rebalance state of each free portfolio (`delete_private_series`) and then the portfolios;
     Markets deletes their weights and values with them. Orders and trades are still deleted
     before `Account.delete`.
8. **Markets records this application as the manager of its signals and portfolios.** Only it
   publishes to them. Who among people may publish stays this application's rule: the account's
   owner (ADR 0028).
9. **Orders and trades stay private instances for now.** Nothing reads them. They follow the
   same rule when the ms-markets execution engine's recorder exists: recorded in Markets'
   tables through the API.

## Rollout

One release, in each Environment, in this order (owner's decisions of 2026-11-20). A person runs
every step that is not a push; the [runbook](../operations/markets-api-boundary.md#5-markets-reads-no-table-of-this-application)
has the commands.

1. The Markets API runs ms-markets 4.2.0, and its migration has run.
2. Hold the scheduled portfolio runs, as in step 2 of the runbook, and ask the applications that
   create portfolios to wait until step 5.
3. Delete every existing portfolio with this application's portfolio delete, before the push:
   left in place, a portfolio would run on this release and recompute its rebalance state under
   the new trading rule next to its old rows. The delete retires its `Portfolio`, which needs no
   manager, so the rows created before ms-markets ADR 0060 need no assignment; it deletes the
   configuration, its own bars configuration, its inline rebalance configuration and its signal
   definition (ADR 0027, ADR 0028).
4. Push this release. Its migration Job applies revision 0033, which drops the three instances
   with every row of the deleted portfolios' signals, weights and values, and removes the
   instances' memberships of `alpaca-connectors-markets`, which the code no longer declares.
5. Release the scheduled runs and the applications. They create their portfolios again as
   usual; check one create, one publication of weights and one run.
6. The owner of `alpaca-connectors-markets` revokes the `MainSequenceMarkets` branch's
   `data_viewer` and removes the namespace.

There is no later release for this ADR. The release before this one cannot run on ms-markets
4.2.0, and after revision 0033 the old series are gone, so going back means going forward: the
revision's downgrade recreates the three tables empty.

## Consequences

- A new Environment needs no person to give Markets access. Issue #37's failure cannot happen.
- Markets serves this application's portfolios from its own tables, and nobody reads this
  application's tables through Markets.
- A run and a publication of weights need the Markets API for their reads and writes. A stopped
  run continues from what Markets holds, and repeating a publish writes nothing.
- Published weights and values are visible to everyone who may read Markets.
- This application migrates three instance tables fewer (revision 0033), and loses its history
  reads, its catalog registration, its access tooling and its history moves (`instances backfill`
  and `instances rollback`).
- The weights routes, the run triggers, the delete rules and every error code but
  `signal_not_registered` stay as they are.

## Implementation Notes

Checked against ms-markets 4.2.0 on 2026-11-20. Every recipe call is a composite of
`alpaca_connectors.markets_api`, whose docstring names the call, its answer and its refusals.

- **The not-after-last rule stays checked here.** `publish_signal_weights` refuses an observation
  at or before the signal's last stored time only when it differs from what is stored; an equal
  one is skipped and the call succeeds (ms-markets `services/series.py`, `publish_series`). This
  application refuses both with 409 `observation_not_after_last`, so it reads the signal's last
  time with `signal_history` first, as before. The recipe refuses a time conflict and an invalid
  value with the same `ValueError`, so a refusal is answered as a time conflict when the stored
  last time, read again, is at or after the observation, never from the message.
- **The run's `Portfolio` is this application's.** `run_portfolio` resolves the row through a
  `portfolio_resolver`; this application's resolver upserts `ALPACA_PORTFOLIO__<hex>` on the
  configuration's calendar, as before. The recipe starts a first run at the signal's first
  observation and reads only its signal.
- **`portfolio_preflight` is not used.** It takes one built portfolio configuration and reads one
  signal's bounds per call, while this application's preflight checks every scheduled portfolio
  with one read per kind (issue #46) before building anything, with its own message.
- **The run summary** counts only the published weights and values: the recipe does not count the
  rows of prices, calendar events or rebalance state, so those entries are null.
- **Calendar events for both strategies (owner, 2026-11-20).** Since ms-markets 4.1.0
  `ImmediateSignal` requires `calendar_events_instance` and `calendar_identifier`
  (`rebalance_strategy/immediate_signal.py`, lines 44 and 52), and refuses an events dependency
  that does not publish both `market_open` and `market_close` (`_require_session_events`, line
  74); `CalendarEventSignal` inherits both (`rebalance_strategy/calendar_event.py`, line 12). The
  run builds one `PortfolioCalendarEvents` with both events for either strategy.
  - Its identity: `event_types` is a field of `PortfolioCalendarEventsConfiguration`
    (`time_index/portfolios/calendar_events.py`, line 35) and the whole configuration enters the
    updater's `update_hash`, as `offset_start` does (MetaTables
    `updaters/configuration.py`, `TimeIndexTableUpdateConfig`). Every existing portfolio's
    calendar-events updater therefore gets a new identity with no update statistics.
  - Its rows: a first run writes every session of the calendar from the updater's
    `OFFSET_START`, 2018-01-01 or the calendar's first date, to now (`calendar_events.py`, lines
    55 and 70-74), into the same private table. Rows already there (the configured event of the
    same time, calendar, session and type) are written again with equal values: MetaTables
    overwrites on its unique index (`metatables/models.py`, `upsert_data_into_table`). So the
    history gains its opens and closes from the first run of this release, with no person's step.
- **Existing portfolios are deleted (owner, 2026-11-20).** ms-markets 4.1.0 gives every
  portfolio configuration a new hash (`fill_rule`, `execution_policy`). A portfolio that is not
  deleted keeps its `ALPACA_PORTFOLIO__<hex>` row, but its `PortfolioRebalance` gets a new
  identity and recomputes the private rebalance state from the signal's first observation under
  the new rule (`time_index/portfolios/rebalance.py`, lines 104-112 and 221-243), next to the rows
  of the earlier rule, and `PortfolioWeights` reads every rebalance row of the portfolio after its
  last published weights (`weights.py`, lines 171-183). So each one is deleted before the push,
  with this application's portfolio delete (`DELETE /v1/portfolio-configurations/{uid}` or
  `alpaca-connectors portfolio delete <uid> --execute`):
  - it works on rows that have no manager: retiring checks none (ms-markets
    `msm/services/portfolio_lifecycle.py`, `retire_portfolio_record`), while a Markets portfolio
    delete, a signal delete and a publish refuse such rows (`msm/api/managers.py`,
    `manager_refusal`);
  - in this application's tables it deletes the configuration, its own bars configuration, its
    inline rebalance configuration and its signal definition, and records the retired
    `Portfolio` (`EndedPortfolio`);
  - the retired `Portfolio`, its `SignalMetadata` and its private rebalance-state rows stay with
    its account's history, as every deleted portfolio's do (ADR 0027, ADR 0028), and its old
    signal weights, weights and values go with the instances in revision 0033. The calendar
    events belong to the calendar, not the portfolio, and stay.

- **Every other ms-markets 4.1.0 and 4.2.0 change checked:** the required
  `PortfolioBuildConfiguration.valuation_asset_identifier` is `USD`, the cash asset this
  application registers for every Alpaca account (`holdings/account.py`); the execution policy
  keeps ms-markets' defaults; `fail_on_missing_prices` still refuses a price older than the
  configured staleness (`fail_on_missing_values`, whose default changed to `False`, is always
  given); `Portfolio.delete` deletes nothing since 4.2.0, so the failed-create cleanup calls
  `DELETE /portfolio/{uid}/`; the portfolio delete preflight lists each portfolio with its own
  blockers and labels them by unique identifier, which `portfolios_delete_blockers` reads; the
  rebalance-state instance gets ms-markets' `(portfolio_identifier, time_index)` index in
  revision 0033. A `valuation_column` ms-markets cannot trade at (`open`, `high`, `low`, `vwap`:
  a trade's price is a close or a mark) is refused at create and update with ms-markets' own
  rule (`require_observed_price_column`), answered `422`; one already stored fails at its run
  with the same reason. Tests build the real ms-markets configuration and strategy of each rule
  offline (`test_both_strategies_read_the_calendar_sessions_and_build_with_ms_markets`).

## Open Questions

- Whether `alpaca_connectors__signal_asset` (every asset a signal has published) is still
  needed once Markets answers the same from the series. It stays until that is checked against
  the run's pricing scope and the bars scope.
- How long a run of a long history takes through the Markets API. Not measured.
