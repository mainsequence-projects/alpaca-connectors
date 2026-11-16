# ADR 0024: Portfolios Built From Weights An Application Provides

## Status

Proposed on 2026-11-16 for
[issue #30](https://github.com/mainsequence-projects/alpaca-connectors/issues/30), for the owner's
review. Nothing is implemented. ms-markets 3.8.0, released on 2026-11-16, ships the signal it
needs (`ApplicationWeights`,
[MainSequenceMarkets#75](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/75)),
and this plan is written against that release. Implementation waits for the owner's answers under
[Open Questions](#open-questions).

The owner decided two points on 2026-11-16:

- **Who may do it:** the creator owns the portfolio (decision 6).
- **Bars:** the caller names a bars configuration. Explicit `assets` is too general for this case.
  What is needed is a bars configuration that updates every asset its portfolios need
  (decision 5). Plan it; do not implement it yet.
- **Inline bars:** the create call may also create its `portfolios` bars configuration in the
  same request (decision 5, open question 1, decided the same day).
- **Live portfolios only:** a deleted or retired portfolio's assets are no longer updated
  (decision 5).
- **The asset read must stay fast** on a large signal weights table (decision 5).

It amends:

- [ADR 0002](0002_etf_holdings_alpaca_portfolio_construction.md): a portfolio's weights may also
  be provided;
- [ADR 0003](0003_stored_alpaca_bars_configurations_and_asset_sources.md): a fourth asset source,
  `portfolios`;
- [ADR 0006](0006_universe_backed_alpaca_etf_signal.md) and
  [ADR 0007](0007_one_signal_configuration_per_job.md): a second kind of signal, which has no
  Signal Configuration and no Signal Job;
- [ADR 0008](0008_portfolio_configuration_and_job_ownership.md): a portfolio configuration
  references either an ETF Signal Configuration or provided weights;
- [ADR 0016](0016_order_execution_for_authorised_applications.md) decision 7 and
  [ADR 0019](0019_account_owners_vouched_for_by_trusted_applications.md): a provided-weights
  portfolio has an owner;
- [ADR 0023](0023_account_sync_sizes_and_allocates_funds.md) section 4: its missing-weights case
  narrows.

## Context

### What a portfolio is today

- **One kind.** A row of `AlpacaETFPortfolioConfigurationTable` names an ETF Signal Configuration,
  a Bars Configuration and a Rebalance Configuration (all required), plus valuation and fee
  settings and a `scheduled` flag.
- **The signal is the ETF signal.** `AlpacaETFHoldingsSignal` publishes each Universe extraction
  to `AlpacaSignalWeightsStorage`. Its Signal Job (ADR 0007) is the only producer.
- **The run.** `execute_portfolio_configuration` resolves the configuration, requires a published
  observation, builds the ms-markets graph (interpolated prices, calendar events, rebalance,
  weights, values), upserts the `Portfolio` row as `ALPACA_ETF_PORTFOLIO__<uid hex>`, records
  `portfolio_uid` and registers the five series in the Markets catalog. The 02:00 UTC dispatcher
  starts one run per scheduled configuration (ADR 0013).
- **The sync.** The account sync sizes each portfolio row of the account target from
  `AlpacaPortfolioWeightsStorage` (ADR 0023). A portfolio another application built has no rows
  there, so `size_targets` fails with `portfolio_weights_missing` and nothing is allocated.
- **Who may act.** Any verified caller may create, change, pause, run or delete a portfolio
  configuration. Only bars configurations check ownership, through their account (ADR 0022).

### What bars cover today

A bars configuration fetches one daily profile (`1d/sip/all` or `1d/iex/raw`) with one account's
keys, for one asset source (ADR 0003):

| Source | Assets | Hashed scope |
| --- | --- | --- |
| `assets` | the configuration's membership rows | the explicit identifiers |
| `universe` | an Asset Universe's category | the category identifier |
| `account_holdings` | the account's newest holdings snapshot of the last 30 days | the account UID |

None of them follows the assets a portfolio holds. An `assets` configuration would have to be
edited every time an application gives weights with a new asset, and the edit would be a second
call the application can forget.

### What ms-markets 3.8.0 offers

`msm_portfolios.contrib.signals.ApplicationWeights` is a `SignalWeights` whose observations are
published, never calculated:

- `ApplicationWeightsConfig(provider, key)`: nothing about the weights is in the configuration, so
  the signal UID never changes when the weights do. `provider` keeps two providers' keys apart.
- `publish(observations, *, description=None)`: `observations` maps a UTC time to
  `{asset unique_identifier: weight}`, with timezone-aware times. An unknown asset, a naive time
  or a weight that is not a finite number refuses the whole call, which then writes nothing. One
  observation is the whole set: an asset the previous observation held and this one omits is
  written at `0`. Several observations are one write. An observation at or before the last stored
  time is always refused: published observations are never restated, because portfolios that
  rebalanced on them would keep the old weights. Weights are stored as given.
- `get_asset_list()`: every asset the signal has ever published (its last row per asset, zeros
  included).
- It reads no price, offers no route and decides no trade time. Its refusals are `LookupError`,
  `ValueError` and `TypeError` whose messages name assets and weights.

## Decision

### 1. A second weights source on the same configuration

A portfolio configuration gains `weights_source`:

- `etf_signal`: today's portfolios. `signal_configuration_uid` is required.
- `provided`: weights an application provides. `signal_configuration_uid` is null.

Everything else is shared: the bars, rebalance, valuation and fee settings, `scheduled`, the
`Portfolio` row, the five series, the catalog registration, the portfolio Job and its 02:00
dispatcher. A provided-weights portfolio is one more row of the same table, not a new table, so
every reader of portfolio configurations, the dispatcher and the run see it without a second path.
`AlpacaETFPortfolioConfigurationTable` keeps its name and storage identity.

### 2. The signal is ms-markets' `ApplicationWeights`

- **One signal per portfolio.** Its configuration is `provider` = this connector's MetaTables
  namespace and `key` = the portfolio configuration UID. The signal UID is fixed when the row is
  created and never changes.
- **Output.** `AlpacaSignalWeightsStorage`, beside the ETF signals. No new table.
- **No Signal Configuration, no Signal Job.** Observations are written by the API call that
  provides them. The ETF signal class, `SIGNAL_IDENTITY_CLASS_IMPORT_PATH` and every existing
  signal UID are unchanged.
- **The connector writes no signal class of its own.** The class path is ms-markets', so the UIDs
  do not depend on where this connector keeps its modules.

### 3. The portfolio's identity

- Unique identifier `ALPACA_PORTFOLIO__<configuration uid hex>`. ETF portfolios keep
  `ALPACA_ETF_PORTFOLIO__<uid hex>`.
- **Creating the configuration creates the `Portfolio` row** (`Portfolio.upsert` with the
  rebalance calendar), so the create answer names it: `portfolio_uid` and
  `portfolio_unique_identifier`. The row is otherwise the same row the first run would upsert.
  The catalog registration and the series still come from the first run.

### 4. The routes

All under the existing `/v1/portfolio-configurations` group. One observation is
`{observed_at, weights: {<asset unique_identifier>: <weight>}}`.

| Route | Effect |
| --- | --- |
| `POST /v1/portfolio-configurations/provided-weights` | create: `name`, `description?`, either `bars_configuration_uid` or `bars_configuration` (a new `portfolios` one), `rebalance_configuration_uid`, the valuation and fee settings, `scheduled?`, `observations` (one or more) |
| `POST /v1/portfolio-configurations/{uid}/weights` | give it new weights: `observations` (one or more) |
| `GET /v1/portfolio-configurations/{uid}/weights` | the stored observations, newest first, as the ETF signal history reads them |
| `PATCH /v1/portfolio-configurations/{uid}` | as today; pause and resume through `scheduled` |
| `DELETE /v1/portfolio-configurations/{uid}` | as today; retire it |
| `POST /v1/portfolio-configurations/{uid}/actions/run` | as today |

- **Create** creates the inline bars configuration when one is given, stores the row, publishes
  the observations and creates the `Portfolio` row, in that order. A refused publication deletes
  the rows it just stored (the portfolio configuration, and the bars configuration it created),
  so a failed create leaves nothing.
- **Give it new weights.** Each call is one more observation of the same signal; the portfolio
  rebalances to it at its next event. An asset the new weights drop is written at `0` by
  `publish`. Several observations in one call start or extend a history in one write. A history
  can only be extended: an observation at or before the last one is refused, and a portfolio
  whose history was wrong is retired and created again.
- **Weights are stored as given.** No normalisation; whether they sum to one or may be negative is
  the application's rule, as in MainSequenceMarkets#75. The account sync sizes them as they are.
- **Asset identifiers** are Main Sequence `unique_identifier` values of registered Alpaca assets
  (`ALPACA::<uuid>`, ADR 0004). One without Alpaca details refuses the call with
  `asset_not_alpaca`, because no bars can be fetched for it.
- **The PATCH** cannot change `weights_source` or set `signal_configuration_uid` on a
  provided-weights row.
- **Responses** add `weights_source` to every portfolio configuration. On a provided-weights row
  the detail's `linked_signal` is null and a `provided_weights` block gives the signal UID, the
  first and last observation times and the asset count.
- **Errors.** `portfolio_owned_by_another` (403), `observation_not_after_last` (409),
  `unknown_asset`, `asset_not_alpaca`, `invalid_observation` (a naive time, a weight that is not
  a finite number, or an empty set with nothing to close) and `empty_observations` (422). The
  connector maps the signal's exceptions to these codes; since their messages name assets and
  weights, no answer carries them, and no error names an asset, a weight or an owner.

### 5. Bars: a `portfolios` asset source

A bars configuration gains a fourth source, `portfolios`, whose assets are the assets its
portfolios need:

- **Assets.** At run time, the union over every live portfolio configuration that names this
  bars configuration, of every asset its signal has ever published. Both weights sources count:
  an ETF portfolio may name a `portfolios` bars configuration too.
- **Live means not deleted and not retired (owner).** A deleted configuration has no row, so it
  drops out by itself. A configuration whose `Portfolio` is retired (`retired_at` at or before
  the run's time, ms-markets ADR 0049) is filtered out, because a retired portfolio's updaters
  write nothing and need no new bars. A paused configuration (`scheduled = false`) still counts:
  it can still be run on demand. A configuration whose `Portfolio` does not exist yet (an ETF
  portfolio before its first run) counts.
- **Ever published, not only the latest.** A portfolio sells a dropped asset at the event after
  the observation that dropped it, and is valued at that price. A portfolio created with a
  history needs every asset of that history, including one the history already dropped.
- **Assets leave the list only with their portfolio.** An asset stays fetched while any live
  portfolio of the bars configuration has ever published it, and leaves at the next run after the
  last such portfolio is deleted or retired. The bars already stored stay.
- **The list itself is stored nowhere.** Each resolution derives it; `POST
  /v1/bar-configurations/{uid}/actions/resolve` shows the list a run would use.

**The read is bounded by the number of assets, not the number of observations (owner).**
`AlpacaSignalWeightsStorage` grows with every observation of every signal: an ETF signal adds
about 500 rows a day. Its only index is the contract's unique
`(time_index, signal_uid, asset_identifier)`, which leads with time, so a filter by `signal_uid`
cannot use it. "Every asset this signal ever published" read from that table
(`ApplicationWeights.get_asset_list()`, a `DISTINCT ON (signal_uid, asset_identifier)` over the
rows) scans the whole table at every bars run, and grows without bound. The resolution never
reads it. Instead:

- **A signal-asset table.** `alpaca_connectors__signal_asset` holds one row per signal and asset
  it ever published: `signal_uid`, `asset_identifier`, `first_published_at`, with the primary key
  `(signal_uid, asset_identifier)`. Its size is the number of distinct pairs, a few hundred per
  ETF signal and a handful per provided-weights portfolio, whatever the number of observations.
- **Written before every publication**, by the two places that publish: the provided-weights
  service before `ApplicationWeights.publish`, and the ETF Signal Job before its signal update.
  The write inserts the pairs it does not hold yet (insert, ignoring conflicts on the key). Writing
  first keeps the table a superset of what is published: a publication that then fails leaves at
  worst an asset fetched that no portfolio holds yet, never a held asset without bars.
- **Filled once for existing signals** by revision `0025` itself, from one `SELECT DISTINCT
  signal_uid, asset_identifier` over `AlpacaSignalWeightsStorage`. The migration Job runs it
  (ADR 0012); it is the only full scan.
- **The resolution is three bounded reads:**
  1. the portfolio configurations with `bars_configuration_uid = P`, through the existing index on
     that column, reading only `uid`, `weights_source`, `signal_configuration_uid` and
     `portfolio_uid`;
  2. their `Portfolio` rows by `uid IN (...)` in one `msm.api` row call, reading `retired_at`
     (the primary key serves it), and, for ETF rows, their Signal Configurations in one query
     (`signal_job_configurations_by_uids`). Signal UIDs are then computed in memory: a
     provided-weights signal from its configuration UID, an ETF signal from its Universe;
  3. `SELECT DISTINCT asset_identifier FROM alpaca_connectors__signal_asset WHERE signal_uid IN
     (...)`, served by the primary key.

  No step reads a time series, and none issues a query per portfolio.
- **No membership rows and no Universe.** The bars configuration names neither. Giving a portfolio
  new weights is the only call an application makes; the next bars run picks the new assets up,
  with no edit to the bars configuration.
- **Keys and profile** come from the configuration as today: `account_uid` selects the keys, and
  `frequency_id`, `feed` and `adjustment` the profile. Creating one needs the account's owner
  (ADR 0022), as for every bars configuration.
- **Update identity.** The hashed scope is the bars configuration's UID, as `account_holdings`
  hashes its account: membership changes never mint a new updater, and each newly added asset
  backfills on its own incremental statistics from the updater's first-run floor (2018-01-01),
  which covers a history a portfolio starts with.
- **Resolution** reads the portfolio configurations, the `Portfolio` rows and the signal-asset
  table only. It does not contact Alpaca, runs no signal and reads no time series. A configuration that resolves to no assets (no portfolio
  names it, or none has published) fails its run with the existing "resolves to no assets" error,
  and the bars preflight reports it.
- **Order of the day.** Bars run at 00:00 UTC and portfolios at 02:00 UTC, so weights given during
  a day get their bars before the next scheduled portfolio run. A caller who runs the portfolio at
  once after adding an asset should run the bars update first; otherwise the run fails on missing
  prices when `fail_on_missing_prices` is set.

A provided-weights portfolio may name any enabled bars configuration, as an ETF portfolio may
today. The `portfolios` source is the one that covers whatever weights it is given.

**Created in the same request (owner).** Instead of `bars_configuration_uid`, the create call may
carry `bars_configuration`: `name`, `description?`, `account_uid`, `frequency_id`, `feed`,
`adjustment`, `scheduled?`. The connector creates a `portfolios` bars configuration from it
through the same service as `POST /v1/bar-configurations`, so the same rules apply: the caller
must be the account's owner (ADR 0022), and the profile must be one `storage_for` knows. The two
fields are exclusive; one is required.

**One bars table per profile.** A `portfolios` bars configuration writes the same storage as
every other bars configuration of its profile (`alpaca_connectors__bars_1d_sip_all` or
`alpaca_connectors__bars_1d_iex_raw`, keyed by `time_index` and `asset_identifier`). Its assets
are only the selection of what to fetch. An asset that another configuration already updates
is not fetched twice from scratch: its incremental statistics are per asset in that table, and
the interpolated prices read that one table whichever configuration filled it.

### 6. Who may do it (owner)

- **Any verified caller may create one.** The acting owner at creation (ADR 0019, the person pair
  `(owner_gateway_uid, owner_user_uid)`) is stored on the row.
- **Only that owner** may give it weights, pause or resume it, change it, or delete it. Any other
  caller is refused with `403 portfolio_owned_by_another`. A requester-bound Agent call acts for
  its requester, so the Agent reaches only its person's provided-weights portfolios.
- **Reads and runs stay open**, as for ETF portfolios.
- **ETF portfolio rows have no owner** and keep today's rules. Nothing here adds an owner to them.

### 7. The Agent

No new tool. Providing weights is an application capability, as order execution is (ADR 0016
decision 8). The Agent's portfolio tools list and read provided-weights portfolios like any other,
show `weights_source`, and cannot create one. Its pause and delete tools reach one only for its
owner (decision 6).

### 8. The account sync

No code changes. A provided-weights portfolio publishes `AlpacaPortfolioWeightsStorage` rows under
its `portfolio_identifier` like any other, so the sync finds them. `portfolio_weights_missing` is
left for a portfolio that has not run yet. ADR 0023 section 4 gets a note.

## Worked Example

Two provided-weights portfolios, A (owned by application X) and B (owned by application Y), both
name the same `portfolios` bars configuration P (`1d/sip/all`). Times are UTC; weights are
illustrative.

**Monday 16.** A is created with `{NVDA: 0.6, JPM: 0.4}` observed at 14:00, and B with
`{JPM: 0.7, MSFT: 0.3}` at 15:00.

| Table | Rows |
| --- | --- |
| Configuration rows | A, B and P. None of them changes in this example. |
| `…__signalweightsts__main` | `(16 14:00, sig_A, NVDA, 0.6)`, `(16 14:00, sig_A, JPM, 0.4)`, `(16 15:00, sig_B, JPM, 0.7)`, `(16 15:00, sig_B, MSFT, 0.3)` |
| P's resolved assets | NVDA, JPM, MSFT |
| `…__bars_1d_sip_all` | one row per day and asset, fetched at 00:00 on the 17th. JPM is fetched once, though both portfolios hold it. |
| `…__portfolioweightsts__main` | at the 16th's close: A NVDA 0.6, A JPM 0.4, B JPM 0.7, B MSFT 0.3 |

**Wednesday 18.** X gives A `{NVDA: 0.5, AAPL: 0.5}` at 14:00.

- **Signal weights.** Three rows for `sig_A` at 14:00 on the 18th: NVDA 0.5, AAPL 0.5, and JPM 0,
  written by `publish` because the new set drops it. `sig_B` gets nothing.
- **P does not change.** Its resolved assets become NVDA, JPM, MSFT, AAPL. JPM stays, because B
  holds it and A once did.
- **Bars.** At 00:00 on the 19th, P's run fetches the 17th and 18th for NVDA, JPM and MSFT, and
  AAPL from the updater's floor (2018-01-02) to the 18th, since the table has no AAPL rows yet.
  Another configuration of the same profile that already holds AAPL would make this backfill
  unnecessary: the table, not the configuration, records what is stored.
- **Portfolio weights.** At 02:00 on the 19th, A's run rebalances at the 18th's close to NVDA
  0.5, AAPL 0.5 and JPM 0 (before: 0.6 and 0.4). B rebalances to its unchanged weights.
- **The account sync** sizes A's fund from A's rows at the 18th's close; B's fund is unaffected.

The dimensions: configurations are one row per portfolio or bars configuration. Signal weights
are keyed by time, signal and asset. Bars are keyed by time and asset only, shared by every
configuration and portfolio of the profile. Portfolio weights are keyed by time, portfolio and
asset.

## Schema

One revision, `0025`, adds and relaxes only:

- portfolio configuration: `weights_source` (`String(16)`, not null, server default `etf_signal`,
  check `IN ('etf_signal', 'provided')`); `signal_configuration_uid` becomes nullable, with a check
  that it is set exactly when `weights_source = 'etf_signal'`; `owner_gateway_uid` (`String(64)`)
  and `owner_user_uid` (`String(255)`), nullable, set together, and required when
  `weights_source = 'provided'`.
- bars configuration: the `asset_source` check admits `portfolios`.
- `alpaca_connectors__signal_asset` (new): `signal_uid` (`String(255)`), `asset_identifier`
  (`String(255)`), `first_published_at` (`DateTime(timezone=True)`), primary key
  `(signal_uid, asset_identifier)`. The revision fills it from `AlpacaSignalWeightsStorage` with
  one `INSERT ... SELECT DISTINCT`.

The server default keeps a release that predates the column inserting ETF rows.

## Implementation Plan

After the ms-markets release and the open questions:

1. Raise `ms-markets` to `>=3.8.0,<4` (and the `etfhextractor` override with it).
2. Revision `0025` and the table and row model fields.
3. `alpaca_connectors.portfolios`: build the `ApplicationWeights` signal for a row; the create and
   publish services with the owner check; `resolve_portfolio_configuration` and
   `build_portfolio_graph` branch on `weights_source`; the identifier of decision 3.
4. `alpaca_connectors.market_data`: the `portfolios` source in `AlpacaStockBarsConfig`,
   `validate_configuration_scope`, `build_market_data_update` and the bars preflight, with the
   three-read resolution of decision 5; the signal-asset write in the provided-weights service and
   in the ETF Signal Job.
5. API schemas, the three routes, `weights_source` and the detail block; the bars source in the
   bar configuration schemas.
6. Agent tools: `weights_source` in results, nothing else.
7. Docs: [API](../api.md), the portfolio and bars pages, [Jobs](../operations/jobs.md), the
   capability model, and the status notes of the ADRs this one amends.
8. Tests: create with one observation and with a history; new weights dropping an asset; the
   refusal of an earlier observation and of a non-Alpaca asset; a failed create leaves no row; the
   owner check on weights, PATCH and DELETE, including a vouched external owner; the run builds
   the `ApplicationWeights` graph and registers the series; a `portfolios` bars configuration
   resolves the union of its live portfolios' assets, leaves out a deleted and a retired
   portfolio, keeps a paused one, and fails on none; the resolution issues a fixed number of
   queries whatever the number of portfolios and observations, and never reads the signal weights
   table; a publication that fails after the signal-asset write leaves the table a superset; the sync sizes a
   provided-weights portfolio.

## Open Questions

1. **Create the bars configuration inline?** Decided on 2026-11-16 (owner): yes, see
   decision 5.
2. **Must naming a bars configuration need its account's owner?** Naming a `portfolios` bars
   configuration adds assets to what it fetches with that account's keys, which is configuring it
   in ADR 0022's sense. Today naming any bars configuration needs nothing, and this plan keeps
   that.

3. **An ms-markets index.** `ApplicationWeights.publish` reads each asset's last row with the same
   `DISTINCT ON` over `AlpacaSignalWeightsStorage`, so every publication scans the table too. The
   connector cannot fix that without departing from the `SignalWeightsTS` contract. Asked of
   ms-markets on 2026-11-16 at the owner's request:
   [MainSequenceMarkets#77](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/77)
   proposes `(signal_uid, asset_identifier, time_index)` on `SignalWeightsTS` and
   `(portfolio_identifier, asset_identifier, time_index)` on `PortfolioWeightsTS`, which the
   account sync reads. Not a blocker: this plan works without it, and a release with it needs
   a revision here for the instances.

## Consequences

- An application builds a portfolio of Alpaca assets here, with one call to create it and one per
  set of weights, and the account sync can fund it. It no longer needs its own portfolio engine on
  a copy of this connector's prices.
- One portfolio table, one run, one dispatcher and one signal table hold both kinds.
- A bars configuration can follow its portfolios instead of a list someone edits.
- Provided-weights portfolios are the first portfolio configurations with an owner.
- A provided history is append-only, as ms-markets 3.8.0 decides: a mistaken observation is
  corrected by a new one, or by retiring the portfolio.
