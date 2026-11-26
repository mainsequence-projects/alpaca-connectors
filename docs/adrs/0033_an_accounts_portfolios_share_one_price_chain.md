# ADR 0033: An Account's Portfolios Share One Price Chain

## Status

Proposed on 2026-11-26, from the owner's design in the session that moved this application to
ms-markets 6.2.0. Not implemented. It changes how a portfolio run gets its prices
([ADR 0029](0029_interpolated_price_tables_are_named_like_every_project_table.md)) and where a
portfolio's bars are fetched (ADR 0028, decision 4, and
[ADR 0021](0021_daily_bars_dispatcher.md)). It builds on decision 9 of
[ADR 0013](0013_workflow_declared_configuration_jobs.md#9-one-run-per-tick): the dispatcher's tick
runs every scheduled portfolio in one ms-markets `run_portfolios` call. No ms-markets change is
needed.

## Context

### What a portfolio run builds today

Each portfolio builds its own chain of updaters. MetaTables joins the graphs of one
`run_portfolios` call by update hash, so only identical updates run once.

| Updater | Configuration that enters its update hash | Writes |
|---|---|---|
| `AlpacaStockBarsNode`, outside the graph, before the run | the portfolio's own `bars_configuration_uid` (`AlpacaStockBarsPortfoliosConfig`) | the profile's bars table, e.g. `AlpacaStockBars1dIexRawStorage` |
| `InterpolatedPrices` | `asset_list`: this portfolio's assets; source `TimeIndexTableRef` of the bars table | `AlpacaInterpolatedPrices1dIexRawStorage` |
| `PortfolioCalendarEvents` | `offset_start`: this portfolio's first signal observation; the calendar | `AlpacaPortfolioCalendarEventsStorage` |
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

### What ms-markets 6.2.0 offers

- `InterpolatedPrices.get_asset_list()` takes the constructor's `asset_list`, else resolves
  `asset_category_unique_id`, else asks the source updater. The constructor refuses neither a list
  nor a category; it refuses both missing (`assert asset_list is not None` unless a category is
  set).
- Resolving a category is refused inside msm (`require_asset_category_scope`: "cannot resolve
  asset category … inside msm. Resolve the category through MetaTable services").
- `InterpolatedPrices` is checked by its contract and its output table, never by its exact type,
  so a subclass is accepted as a portfolio's valuation source.
- The Markets API creates an asset category (`POST /api/v1/asset-category/`, with an initial
  `assets` list), replaces its whole membership (`PATCH /api/v1/asset-category/{uid}/`, `assets`:
  Asset UIDs) and deletes it. A membership row is keyed by `(category_uid, asset_uid)`, each a
  key to its parent with `ON DELETE CASCADE`. A category records no manager.
- `AssetIndexedUpdater` scopes its statistics to its assets only when a caller passes
  `override_update_stats`, which no node of a joined graph can receive
  ([MainSequenceMarkets#144](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/144)).

## Decision

### 1. Every account owns one asset category

- **The account names it.** `ProviderAccountDetails` (`alpaca_connectors__acct_alpaca`) gets
  `asset_category_uid`: a foreign key to `ms_markets__assetcategory.uid` with
  `ON DELETE RESTRICT`, unique and not null. One account, one category.
- **Its identity comes from the account.** The code creates it with
  `unique_identifier = ALPACA_ACCOUNT_ASSETS__<account uid hex>`, as portfolios are
  `ALPACA_PORTFOLIO__<hex>` (ADR 0024, decision 3), and `display_name` naming the account. Code
  never chooses another name, so a retry finds the category of an account by its identifier.
- **Its members are the account's assets** as `AccountStockBars.get_asset_list()` resolves them
  (decision 2, "Which assets it updates"): every asset ever published by the signal of a live
  portfolio of the account. They are the same for every bars profile, so the account's nodes on
  two profiles write the same members.
- **It is created with the account.** Registration creates the category through the Markets API
  and writes its UID on the account row.
- **It cannot be deleted while the account exists.** The `RESTRICT` key refuses a delete of the
  category, and Markets answers 409 with the database's message. Its members can still be changed
  by another caller of the Markets API, since categories record no manager; ms-markets is not
  changed for this. The next `AccountStockBars` run replaces them with the account's.
- **It goes with the account.** The account delete (ADR 0028) deletes it after `Account.delete`:
  Markets' account delete cascades to the account row, which releases the key, and the category is
  deleted next, its memberships with it. A delete that fails between the two finds the category by
  its identifier on the retry.

### 2. `AccountStockBars` fetches the account's bars, as a node of the run

`AccountStockBars(AlpacaStockBarsNode)` keeps the bars node's fetch, its storage and its
`OFFSET_START`.

- **Its configuration is the account and the bars profile:** `account_uid`, `frequency_id`,
  `feed`, `adjustment`. That is its whole update hash, so it changes only with them.
- **It resolves everything else when it runs, from that configuration.** Its `update()`:
  1. resolves the account's assets (`get_asset_list()`, below);
  2. replaces the category's membership with them, reading their Asset UIDs once
     (`unique_identifier → uid`), since memberships are keyed by UID;
  3. returns no rows, without any Alpaca request, when every asset already holds its last final
     bar (`daily_bars_current_through`, the check the portfolio Job runs today);
  4. otherwise resolves the account's Alpaca keys from `account_uid` (the encrypted columns under
     `ALPACA_CONNECTORS_DATA_KEY`, ADR 0017) and fetches the bars.
#### Which assets it updates

`AccountStockBars.get_asset_list()` answers the account's assets as unique identifiers. It
generalises `resolve_portfolio_bars_scope` (`portfolios/bars_scope.py`), which resolves one bars
configuration's portfolios, to every portfolio of the account. The list is stored nowhere but in
the category, and is resolved once per run, at the start of `update()`:

1. **The account's portfolios.** Every portfolio configuration of the account: those whose
   `account_uid` is the account, and those not yet adopted that name a `portfolios` feed of the
   account (ADR 0028, as amended). One read of the configurations by account, one of the feeds.
2. **Only the ones priced.** A portfolio whose bars configuration (its own, or the feed it names)
   is disabled is left out: its preflight blocks it, so nothing values it. Every bars profile
   counts, not only this node's (decision 1).
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

An empty list (an account with no live portfolio) empties the category, and the run makes no
Alpaca request. The assets are then bound to Alpaca symbols as today (`_resolve_asset_bindings`:
each identifier's ticker and FIGI, resolved with the account's trading client).

- **It runs in any process.** Nothing is attached by the caller: a node Job run that rebuilds it
  from its saved configuration fetches the same bars.
- **It scopes its own statistics** to its assets (`prepare_update_statistics` returns
  `scope_update_statistics_to_assets`), so it is correct inside a joined graph before
  MainSequenceMarkets#144 is released.

### 3. `AccountInterpolatedPrices` interpolates the account's assets

`AccountInterpolatedPrices(InterpolatedPrices)`, one top-level class.

- **Its configuration:** `source_price_instance` is the account's `AccountStockBars` of the
  portfolio's profile, `asset_category_unique_id` is the account's category, and `asset_list` is
  not set. Its hash holds the source's update and the category's identifier, never an asset.
- **It reads its assets from the category** (`get_asset_list()` reads the category's members
  through the Markets API), which `AccountStockBars` has just written, since it is its dependency.
  It needs no second resolution of the account's portfolios.
- **It writes the profile's interpolated-price table**, which ms-markets requires
  (`AlpacaInterpolatedPrices1dIexRawStorage` or `AlpacaInterpolatedPrices1dSipAllStorage`).
- **It scopes its own statistics**, as `AccountStockBars` does.

### 4. A portfolio's run uses its account's chain

- **Its valuation source** is the `AccountInterpolatedPrices` of its account and of its own bars
  configuration's profile. Every portfolio of the account on that profile builds the same two
  updates, so they run once in a tick.
- **The bars run in the graph.** The portfolio Job no longer runs the bars update before the run;
  the graph runs `AccountStockBars` first, as the source of the prices.
- **The calendar events start at a fixed time per calendar**, `AlpacaStockBarsNode.OFFSET_START`,
  not at the portfolio's signal. Every portfolio of a calendar builds the same
  `PortfolioCalendarEvents`; each rebalance still reads only its own window.
- **A portfolio's own bars configuration stays** (ADR 0028, decision 4). It records the
  portfolio's profile and whether it is enabled. Its bars are fetched by its account's
  `AccountStockBars`.

### 5. The daily bars dispatcher runs the same updater

The 00:00 UTC bars dispatcher (ADR 0021) updates a portfolio's bars with its account's
`AccountStockBars` of that profile, not with the portfolio's own configuration, so the bars of an
account have one update record. Account feeds that price explicit assets or holdings are
unchanged.

### 6. Every updater rebuilds in a node Job run

Each class is a top-level class, each configuration holds identifiers only, and nothing is
attached by the calling process, so ms-markets' node check (`node_arguments`) and a node Job run
rebuild them (ms-markets ADR 0067). Running nodes in node Jobs is not decided here.

## Consequences

- **Nodes per tick.** N portfolios of one account on one profile and one calendar make 2N + 3
  nodes instead of 4N, plus the bars update the Job ran outside the graph per portfolio:

  | Portfolios | Today | This ADR |
  |---|---|---|
  | 1 | 4 + 1 bars update | 5 |
  | 3 | 12 + 3 bars updates | 9 |
  | 10 | 40 + 10 bars updates | 23 |

- **No hash moves with an asset.** A new asset of the account changes the category's members,
  not an update hash: the prices, the rebalance and the ledger keep their update records.
- **One rotation, once.** The first run after the change builds new `InterpolatedPrices`,
  `PortfolioCalendarEvents`, `PortfolioRebalance` and `PortfolioEngine` updates. Their tables keep
  their rows and each asset and portfolio continues from its last row; the old update records stay
  in the MetaTables catalog.
- **The bars check becomes a node.** A tick whose bars are current pays the bars node's
  bookkeeping once per account and profile, and makes no Alpaca request, where today it makes one
  read per portfolio.
- **An account on two profiles prices all its assets on both.** The category is the account's,
  and each profile's chain reads all of it.
- **A category's members are not protected.** A Markets caller can change them until the next
  `AccountStockBars` run restores them.
- **A revision adds the column.** It creates each existing account's category through the Markets
  API, as revision 0036 created the `Order` rows, writes its UID, then makes the column not null.
  Only the migration Job applies it (ADR 0012).
- **The lifecycle check** registers an account, so it creates and deletes a category, and its
  cleanup checks that none is left.

## Alternatives Considered

- **A list computed before the run.** No category and no new updater, but every node of the
  account changes update record when one of its signals gains an asset.
- **`InterpolatedPrices` asking its source.** `asset_list=None` with `AccountStockBars` as the
  source would resolve the assets once, in the bars node; the 6.2.0 constructor refuses it.
- **Category resolution in ms-markets.** It would need ms-markets to read categories inside msm,
  which it refuses today; the subclass reads it here instead.
- **One list per bars profile across accounts.** Fewer nodes, but the list changes with every
  account's portfolios, and one account's change moves every account's records.

## Not Decided Here

- Running nodes in node Jobs (ms-markets ADR 0067).
- Whether a portfolio run started from the API also uses its account's chain, or keeps a chain of
  its own.
- A manager on asset categories in ms-markets.

## Verification

To do when it is implemented:

- unit tests: each class's update hash from its configuration only; the category's membership
  replaced from the account's live portfolios; the current-bars check returning no rows without an
  Alpaca request; statistics scoped to the node's assets;
- ms-markets' `node_arguments` and `rebuild_updater` on every node of an account's graph, as
  ms-markets' `test_node_job_rebuild.py` does;
- the revision on SQLite with a stand-in for the category table, and the Markets API call that
  creates the categories stood in;
- the lifecycle check from this machine against the deployed release: a tick of three portfolios
  of one account makes 9 nodes, and no category is left after the cleanup;
- that the Markets API accepts the branch's category create, membership replace and delete, and
  answers 409 to a category delete while its account exists.
