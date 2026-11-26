# ADR 0033: An Account's Portfolios Share One Price Chain

## Status

Proposed on 2026-11-26, from the owner's design in the session that moved this application to
ms-markets 6.2.0. Revised the same day for ms-markets 6.2.1, whose `InterpolatedPrices` takes its
assets from its source updater
([MainSequenceMarkets#145](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/145)):
an account needs no asset category, and nothing is migrated. Implemented on 2026-11-26:
`AccountStockBars` in `alpaca_connectors.market_data.account_bars`, its scope in
`portfolios/bars_scope.py` (`resolve_account_bars_scope`), the chain in
`portfolios/interpolated_prices.py` (`build_account_interpolated_prices`) and
`portfolios/execution.py` (`account_price_chain`), and the bars Job's `portfolios` path in
`market_data/services.py`.

It changes how a portfolio run gets its prices
([ADR 0029](0029_interpolated_price_tables_are_named_like_every_project_table.md)), where a
portfolio's bars are fetched (ADR 0028, decision 4, issue #39 and
[ADR 0021](0021_daily_bars_dispatcher.md)) and where its calendar events start. It builds on
decision 9 of [ADR 0013](0013_workflow_declared_configuration_jobs.md#9-one-run-per-tick): the
dispatcher's tick runs every scheduled portfolio in one ms-markets `run_portfolios` call. It needs
ms-markets 6.2.1 or later and no other ms-markets change.

## Context

### What a portfolio run builds today

Each portfolio builds its own chain of updaters. MetaTables joins the graphs of one
`run_portfolios` call by update hash, so only identical updates run once.

| Updater | Configuration that enters its update hash | Writes |
|---|---|---|
| `AlpacaStockBarsNode`, outside the graph, before the run | the run's bars configuration UID (`AlpacaStockBarsPortfoliosConfig`): the portfolio's own, or the feed it names before adoption | the profile's bars table, e.g. `AlpacaStockBars1dIexRawStorage` |
| `InterpolatedPrices` | `asset_list`: this portfolio's assets; source `TimeIndexTableRef` of the bars table | `AlpacaInterpolatedPrices1dIexRawStorage` |
| `PortfolioCalendarEvents` | `offset_start`: this portfolio's first signal observation; the calendar and session label | `AlpacaPortfolioCalendarEventsStorage` |
| `PortfolioRebalance` | the whole portfolio configuration, valuation source included | `AlpacaPortfolioRebalanceStateStorage` |
| `PortfolioEngine` (ledger) | the same | `AlpacaPortfolioEventLedgerStorage` |

- **Nothing is shared.** Two portfolios of one account price the same assets through two
  `InterpolatedPrices` updates and read one calendar through two `PortfolioCalendarEvents`
  updates. N portfolios make 4N nodes, and one process runs them one after another.
- **Each node costs its bookkeeping.** In the Production runs of 2026-11-23 every node made 6 to 8
  MetaTables requests (get-or-create, update dependencies, start and end of execution, statistics)
  at 3 to 12 s each, about 40 to 50 s a node.
- **Progress lives in the table, not in the update.** At `set-start-of-execution` the MetaTables
  API (0.1.84) answers `index_progress` from the output table's statistics: the last
  `time_index` of each asset. Every update that writes a table sees the same per-asset progress,
  so a new update of the same table does not recompute the rows already there.
- **A hash with assets in it changes with every new asset.** `asset_list` enters the
  `InterpolatedPrices` hash, and the portfolio configuration, which carries the valuation source,
  enters the rebalance's and the ledger's. A list computed before the run would give every node of
  the account a new update record whenever one of its signals gained an asset.
- **A failed bars update stops nothing** (issue #39). It runs before the graph, and when it fails
  (keys removed, Alpaca unavailable) the run values with the prices already stored and its summary
  says the update failed.

### What ms-markets 6.2.1 offers

- **`InterpolatedPrices` takes its assets from its source updater** when its configuration names
  neither `asset_list` nor `asset_category_unique_id` (#145). The source enters the prices'
  configuration hash by its update hash, so the prices' hash holds the source's configuration and
  no asset the source resolves when it runs. A source that names no assets is refused: a
  `TimeIndexTableRef`, or an updater that is not asset-indexed, when the prices are built; an
  asset-indexed updater whose `get_asset_list()` is None, when the assets are read. 6.2.0 refused
  the source path in the constructor.
- **An updater source is a dependency.** `InterpolatedPrices.dependencies()` returns its source,
  so the source runs in the graph, before the prices.
- **A failed dependency blocks what needs it.** In a `run_portfolios` call, an updater whose
  dependency failed does not run (MetaTables' `blocked`), and each portfolio that needs it is listed
  in `failed`; the others run.
- **Statistics are scoped in a joined graph.** On every run without `override_update_stats`,
  `AssetIndexedUpdater` narrows the table's update statistics to its own `get_asset_list()`, and an
  asset the table does not hold yet starts at its offset start. 6.2.1 does it through MetaTables'
  public hook `prepare_update_statistics`
  ([MainSequenceMarkets#144](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/144));
  6.2.0 did the same through a private method. `InterpolatedPrices` is an `AssetIndexedUpdater`, so
  its statistics are its source's assets'.

## Decision

### 1. No asset enters an update hash

- **The account's price chain is configured by the account and the bars profile.**
  `AccountStockBars` by `account_uid`, `frequency_id`, `feed` and `adjustment` (decision 2), its
  prices by that source and the interpolation settings (decision 3). The portfolio table fixes
  those settings at `1d` and `ffill` (`ck_portfolio_phase_one_upsample_frequency`,
  `ck_portfolio_phase_one_interpolation_rule`), so every portfolio of the account on that profile
  builds the same two updates.
- **The account's assets are resolved when the bars run, and stored nowhere.** No asset category,
  no column, no list on any configuration. The rebalance's and the ledger's hashes carry the
  valuation source, so they hold no asset either.

### 2. `AccountStockBars` fetches the account's bars, as a node of the run

`AccountStockBars` shares the bars node's fetch, its storage and its `OFFSET_START` through
`AlpacaStockBarsFetch`, a mixin with no constructor. It does not subclass `AlpacaStockBarsNode`:
MetaTables saves the arguments of every constructor between an updater's class and its base, so a
subclass would save that node's client and asset-list arguments too. The `portfolios` path of
`AlpacaStockBarsNode` (`AlpacaStockBarsPortfoliosConfig`, `resolve_portfolio_bars_scope`) is
removed.

- **Its configuration is the account and the bars profile:** `account_uid`, `frequency_id`,
  `feed`, `adjustment`. That is its whole update hash, so it changes only with them.
- **It resolves everything else when it runs, from that configuration.** Its `update()`:
  1. reads the account's assets on its profile (`get_asset_list()`, below);
  2. returns no rows, without any Alpaca request, when every asset already holds its last final
     bar (`daily_bars_current_through`, the check the portfolio Job runs today);
  3. otherwise resolves the account's Alpaca keys from `account_uid` (the encrypted columns under
     `ALPACA_CONNECTORS_DATA_KEY`, ADR 0017) and fetches the bars.

#### Which assets it updates

`AccountStockBars.get_asset_list()` answers the account's assets on its profile as unique
identifiers. It generalises `resolve_portfolio_bars_scope` (`portfolios/bars_scope.py`), which
resolves one bars configuration's portfolios, to every portfolio of the account on one profile.
The first call resolves the list (MetaTables' statistics hook, before `update()`), and the
instance keeps it for its later calls and for the prices that read it (decision 3):

1. **The account's portfolios.** Every portfolio configuration of the account: those whose
   `account_uid` is the account, and those not yet adopted that name a `portfolios` feed of the
   account (ADR 0028, as amended). One read of the configurations by account, one of the feeds.
2. **Only the ones priced on this profile.** A portfolio whose bars configuration (its own, or the
   feed it names) is on another profile is left out, since that profile's `AccountStockBars`
   prices it; so is one whose bars configuration is disabled, since its preflight blocks it and
   nothing values it.
3. **Only the live ones.** A portfolio whose `Portfolio` is retired at or before the run's time is
   left out (`retired_portfolio_uids`, one Markets read for all of them; ms-markets ADR 0049). A
   paused one (`scheduled` false) still counts, since a manual run values it, and so does one
   whose `Portfolio` does not exist yet.
4. **Their signals.** Each live portfolio's signal UID, from its signal definition's key, or from
   its configuration UID before adoption (`portfolio_signal_uid`).
5. **Every asset those signals ever published.** One read of the signal-asset table by its
   primary key (`signal_uid`, `asset_identifier`), distinct and sorted. An asset a later
   observation weights at 0 stays, because the portfolio's history holds it and its ledger values
   it.

What is not in the list:

- the account's holdings, and the assets of its `assets` and `account_holdings` feeds, which their
  own feeds update (decision 5);
- the assets of a signal definition with no live portfolio, and of another account's portfolios;
- an asset no signal has published yet. A new set of weights records its assets before it is
  published (`record_signal_assets`), so the next run includes them.

An account with no live portfolio on the profile resolves to an empty list, and the run makes no
Alpaca request. The assets are then bound to Alpaca symbols as today (`_resolve_asset_bindings`:
each identifier's ticker and FIGI, resolved with the account's trading client).

- **It runs in any process.** Nothing is attached by the caller: a node Job run that rebuilds it
  from its saved configuration fetches the same bars.
- **Its statistics are its assets'.** ms-markets scopes them (Context); it does not override
  `prepare_update_statistics`.

### 3. The account's prices take their assets from its bars

The prices are ms-markets' `InterpolatedPrices` itself, with no subclass.

- **Its configuration:** `source_price_instance` is the account's `AccountStockBars` of the
  portfolio's profile, and neither `asset_list` nor `asset_category_unique_id` is set. Its hash
  holds the source's update hash and the interpolation settings, never an asset.
- **Its assets are its source's** `get_asset_list()`: in the run's process, the list the bars node
  resolved; in a node Job run, the rebuilt source resolves it again from its configuration.
- **It writes the profile's interpolated-price table**, which ms-markets requires
  (`AlpacaInterpolatedPrices1dIexRawStorage` or `AlpacaInterpolatedPrices1dSipAllStorage`).
- **Its source is the updater**, not the `TimeIndexTableRef` of the bars table it reads today, so
  the bars are its dependency and run first.

### 4. A portfolio's run uses its account's chain

- **Every run builds it.** A run of one configuration (the API's run action, the CLI) and a run of
  many (the dispatcher's tick) take the same path (ADR 0027).
- **Its valuation source** is the `InterpolatedPrices` of its account and of its own bars
  configuration's profile. The run builds one `AccountStockBars` and one `InterpolatedPrices` per
  account and profile and gives that one to every portfolio of them, so they run once in a tick
  and the account's assets are resolved once in its process.
- **The bars run in the graph.** The portfolio Job no longer runs the bars update before the run
  (`update_portfolio_bars` goes); the graph runs `AccountStockBars` first, as the source of the
  prices, and its current-bars check (decision 2) replaces the Job's.
- **The calendar events start at a fixed time per calendar**, `AlpacaStockBarsNode.OFFSET_START`,
  not at the portfolio's signal. Every portfolio of a calendar and session label builds the same
  `PortfolioCalendarEvents`; each rebalance still reads only its own window. It is in this ADR
  because it is the same move: a shared update whose hash holds nothing of one portfolio.
- **A portfolio's own bars configuration stays** (ADR 0028, decision 4). It records the
  portfolio's profile and whether it is enabled. Its bars are fetched by its account's
  `AccountStockBars`.

### 5. The daily bars dispatcher runs the same updater

The 00:00 UTC bars dispatcher (ADR 0021) updates a portfolio's bars with its account's
`AccountStockBars` of that profile, not with the portfolio's own configuration, so the bars of an
account have one update record. Of two configurations of one account and profile, the run that
comes second finds the bars current and fetches nothing. Account feeds that price explicit assets
or holdings are unchanged.

### 6. Every updater rebuilds in a node Job run

Each class is a top-level class, each configuration holds identifiers only, and nothing is
attached by the calling process, so ms-markets' node check (`node_arguments`) and a node Job run
rebuild them (ms-markets ADR 0067). Running nodes in node Jobs is not decided here.

## Consequences

- **Nodes per tick.** N portfolios of one account on one profile and one calendar make 2N + 3
  nodes (`AccountStockBars`, `InterpolatedPrices`, `PortfolioCalendarEvents`, and each portfolio's
  rebalance and ledger) instead of 4N, plus the bars update the Job ran outside the graph per
  bars configuration:

  | Portfolios | Today | This ADR |
  |---|---|---|
  | 1 | 4 + 1 bars update | 5 |
  | 3 | 12 + 3 bars updates | 9 |
  | 10 | 40 + 10 bars updates | 23 |

- **No hash moves with an asset.** A new asset of the account changes what `AccountStockBars`
  resolves, not an update hash: the bars, the prices, the rebalance and the ledger keep their
  update records.
- **One rotation, once.** The first run after the change builds new bars, `InterpolatedPrices`,
  `PortfolioCalendarEvents`, `PortfolioRebalance` and `PortfolioEngine` updates. Their tables keep
  their rows and each asset and portfolio continues from its last row; the old update records stay
  in the MetaTables catalog.
- **A failed bars fetch stops the account's portfolios on that profile.** `AccountStockBars` is the
  prices' dependency, so when it fails (keys removed, Alpaca unavailable) its prices, rebalances
  and ledgers are blocked for that tick, and each of those portfolios is listed in `failed`, where
  today the run values with the stored prices (issue #39). The other accounts' portfolios run.
- **The bars check becomes a node.** A tick whose bars are current pays the bars node's
  bookkeeping once per account and profile, and makes no Alpaca request, where today it makes one
  read per bars configuration.
- **An account on two profiles** fetches and prices on each profile only the assets of its
  portfolios on that profile, as today.
- **A run of one portfolio** fetches and interpolates every asset of its account on its profile;
  both are incremental, so it pays only for the rows the other portfolios have not written yet.
- **A node Job run of the prices resolves the account's assets again**: the reads of decision 2,
  in that process. In one process they are read once.
- **Nothing is migrated.** No table, column or Markets object is added, the account delete
  (ADR 0028) and the lifecycle check's cleanup are unchanged, and the Markets calls are the reads
  the run makes today.

## Alternatives Considered

- **A list computed before the run.** No new updater, but every node of the account changes update
  record when one of its signals gains an asset.
- **An asset category per account**, this ADR's first draft for ms-markets 6.2.0, whose
  `InterpolatedPrices` refused to take its assets from its source. The bars node wrote the
  category's members, and a subclass of `InterpolatedPrices` read them through the Markets API,
  since msm refuses to resolve a category itself (`require_asset_category_scope`). It spares a node
  Job run of the prices its own resolution, at the cost of a key from the account table to
  `ms_markets__assetcategory`, a revision that creates every account's category, a membership
  write on every bars run, a delete in the account delete, a check in the lifecycle cleanup, and
  members any Markets caller can change. 6.2.1 makes it unnecessary.
- **One list per bars profile across accounts.** Fewer nodes, but the list changes with every
  account's portfolios, and one account's change moves every account's records.

## Not Decided Here

- Running nodes in node Jobs (ms-markets ADR 0067).

## Verification

Done with the implementation (`tests/test_account_price_chain.py`, `tests/test_provided_weights.py`,
`tests/test_portfolio_configurations.py`, `tests/test_portfolio_ticks.py`): two portfolios of one
account make 7 nodes, and every one passes `node_arguments` and rebuilds with its hash, offline.
Not done: the lifecycle check against a deployed release, which needs this release pushed. The
items:

- unit tests: each class's update hash from its configuration only; `AccountStockBars`' assets
  from the account's live portfolios on its profile; the prices' configuration with neither
  `asset_list` nor `asset_category_unique_id`, and their assets the source's; the current-bars
  check returning no rows without an Alpaca request; one bars and one prices update per account and
  profile in a run of several portfolios, and the same chain in a run of one;
- ms-markets' `node_arguments` and `rebuild_updater` on every node of an account's graph, as
  ms-markets' `test_node_job_rebuild.py` does;
- the lifecycle check from this machine against the deployed release: a tick of three portfolios
  of one account makes 9 nodes.
