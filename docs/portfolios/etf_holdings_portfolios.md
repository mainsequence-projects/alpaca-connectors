# ETF Holdings Portfolios

## Goal

Build a durable ms-markets analytical portfolio from an existing ETF Weight Signal, an existing
Alpaca Bars Configuration, persistent interpolated prices, and a reusable Rebalance Configuration.
See [ADR 0008](../adrs/0008_portfolio_configuration_and_job_ownership.md) for Job ownership and
[ADR 0009](../adrs/0009_calendar_event_portfolio_timing.md) for valuation and rebalance timing.

The implementation lives in:

- `src/portfolios/configurations.py`: calculation-only Portfolio and Rebalance MetaTables
- `src/portfolios/execution.py`: reference resolution and portfolio graph execution
- `src/operations/portfolio_jobs.py`: one dedicated Main Sequence Job per Portfolio Configuration
- `src/jobs/run_alpaca_etf_portfolio.py`: repository-local scheduled launcher
- `api/app/routers/portfolio_configurations.py`: thin HTTP CRUD and run surface
- `src/cli/portfolios.py`: thin CLI CRUD and run surface

## Durable Ownership

A Portfolio Configuration stores only calculation intent:

- Signal Configuration reference
- Bars Configuration reference
- Rebalance Configuration reference
- interpolation frequency and rule
- valuation column, maximum permitted valuation staleness, and missing-price policy
- commission-fee assumption
- linked canonical `PortfolioTable.uid` after materialization
- linked dedicated `Job.uid`

The Main Sequence Job exclusively stores execution path, schedule, CPU, memory, spot preference,
maximum runtime, automatic deployment, and image association. A JobRun exclusively stores an
execution's status, timestamps, resolved image/commit, logs, and failure state. None of those
operational values are copied into the Portfolio Configuration MetaTable.

The selected Signal Configuration already resolves its Universe and runtime Alpaca Account. The
selected Bars Configuration already resolves its asset source and raw bars profile. A Portfolio
Configuration therefore stores neither Universe UID, Account UID, nor raw bars TimeIndexMetaTable
UID.

## Execution Graph

```text
Signal Configuration ───────────────> published ETF Weight Signal

Bars Configuration ─> raw Bars ─> persistent InterpolatedPrices

Rebalance Configuration ────────────> PortfolioCalendarEvents ─> CalendarEventSignal

ETF Weight Signal + CalendarEventSignal ─> PortfolioRebalance
                                         └> PortfolioWeights

PortfolioWeights + InterpolatedPrices ──> PortfoliosDataNode ─> PortfoliosStorage
```

The portfolio Job does not re-extract the ETF Universe and does not run the bars updater as a
dependency. It requires an existing Signal observation, then explicitly updates persistent
interpolation, persisted calendar events, rebalance decisions, executed weights, and portfolio
valuation with `update_tree=False`. Signal and raw bars producers remain separate Jobs with their
own schedules and execution histories.

## Rebalance And Valuation Semantics

The supported strategy is `CalendarEventSignal`. Its reusable Rebalance Configuration stores the
persisted calendar identifier, session label, market-open or market-close event, offset, and
every-session or weekly cadence. At each eligible real calendar event, it selects the latest signal
observed at or before that event. A signal observed after the close is therefore first eligible at
the next configured event. Early closes use their actual persisted close timestamps; they are not
normalized to midnight or a nominal close.

The ETF Weight Signal timestamp records when the connector observed the provider holdings. It does
not guarantee the weights became economically effective at that exact instant. The resulting
portfolio is therefore a calendar-executed observation reconstruction, not a perfect point-in-time
ETF replication. It does not model partial fills, volume participation, market impact, or slippage
beyond the configured commission fee.

`InterpolatedPrices` remains a persistent valuation dependency. Interpolation can provide a value
at a real portfolio timestamp, while `valuation_maximum_staleness_seconds` bounds how old that
selected price may be. The missing-price flag controls whether an incomplete valuation fails the
run. Neither option invents daily timestamps, extends the calculation to the current time, or
changes signal validity. `PortfoliosDataNode` writes only at valuation-source observations and
`PortfolioWeights` writes only at rebalance execution events. The connector passes every asset to
`InterpolatedPrices` with the Rebalance Configuration's calendar identifier, so daily valuation
observations follow the configured exchange session instead of the ms-markets `24/7` default used
for bare string asset identifiers.

## CLI

Create the reusable rebalance policy once:

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

Create a Portfolio Configuration and its dedicated Job together:

```bash
alpaca-connectors portfolio create \
  --name "Daily IVV analytical portfolio" \
  --signal-configuration-uid <SIGNAL_CONFIGURATION_UID> \
  --bars-configuration-uid <BARS_CONFIGURATION_UID> \
  --rebalance-configuration-uid <REBALANCE_CONFIGURATION_UID> \
  --valuation-maximum-staleness-seconds 86400 \
  --schedule-type crontab \
  --schedule-expression "30 8 * * 1-5" \
  --schedule-timezone America/New_York \
  --cpu-request 0.25 \
  --memory-request 0.5
```

List, inspect, update, run, inspect executions, and delete:

```bash
alpaca-connectors portfolio list
alpaca-connectors portfolio get <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio update <PORTFOLIO_CONFIGURATION_UID> --commission-fee 0.0002
alpaca-connectors portfolio run <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio runs <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio delete <PORTFOLIO_CONFIGURATION_UID>
```

Prepare and verify the persistent interpolation tables for every migrated Alpaca bars profile:

```bash
alpaca-connectors portfolio prepare-interpolated-prices
alpaca-connectors portfolio prepare-interpolated-prices --check-only
```

The preparation command resolves all registered Alpaca bars tables in one catalog request, derives
the ms-markets dynamic storage identities, generates one real Alembic revision when needed, applies
the shared project migration history, and verifies every resulting `TimeIndexMetaTable`. Normal
portfolio execution only checks and attaches those tables; it never creates schema.

There is no environment argument. The CodeRepositoryBranch runtime resolves its Environment.
Manual and scheduled portfolio JobRuns receive no business arguments; the launcher resolves the
Portfolio Configuration from `JOB_RUN_UID -> Job.uid -> Portfolio Configuration.job_uid`.

## API

The API exposes:

- `GET|POST /v1/portfolio-rebalance-configurations`
- `GET|PATCH|DELETE /v1/portfolio-rebalance-configurations/{uid}`
- `GET /v1/portfolio-rebalance-configurations/discovery`
- `GET|POST /v1/portfolio-configurations`
- `GET|PATCH|DELETE /v1/portfolio-configurations/{uid}`
- `GET /v1/portfolio-configurations/discovery`
- `POST /v1/portfolio-configurations/{uid}/actions/run`
- `GET /v1/portfolio-configurations/{uid}/runs`

Create requests nest schedule and compute values under `job`. Responses compose the durable row
with a live Job projection. Schedule and compute updates patch only the Job.

The configuration detail `GET` resolves the linked Signal, Universe, Alpaca accounts, Bars source,
Rebalance policy, live Job settings, and canonical Portfolio metadata. With the optional
`observation_limit` query parameter (default 2,500, maximum 5,000), it also returns the most recent
portfolio values from shared `PortfoliosStorage`, ordered chronologically for presentation. The
storage query is constrained by the canonical `PortfolioTable.unique_identifier`; it does not scan
or combine values from other portfolios.

The API calculates a quick historical-performance summary with `empyrical-reloaded` from the
returned daily value window. It reports compounded and annualized return, annualized volatility,
Sharpe, Sortino, maximum drawdown, Calmar, best/worst daily return, and positive-period ratio using
252 periods per year and a 0% risk-free rate. It returns the full stored observation count and a
truncation flag so a bounded window is never mislabeled as since-inception history. Alpha and beta
remain absent until a benchmark is explicitly part of the Portfolio Configuration.

## Required Platform State

!!! note "Duration hashing fix"
    [MainSequenceMarkets issue
    5](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/5) was fixed in
    ms-markets 1.0.8 together with Main Sequence SDK 8.1.7. This repository uses ms-markets 1.0.13
    and Main Sequence SDK 8.1.8. Version 1.0.10 fixes the set-based seed-observation query used by
    `PortfolioRebalance` when signal storage has both `signal_uid` and `asset_identifier`
    dimensions. Version 1.0.11 normalizes published time-index and timestamp columns to nanosecond
    UTC, including `open_time`. Version 1.0.12 keeps strict valuation for held assets and real
    entries/exits but does not require a fresh price for a constituent whose previous and current
    weights are both zero. Version 1.0.13 uses `asset_identifier` as the canonical interpolation
    scope key and removes out-of-range seed weights before enforcing unique coordinates.
    Calendar-event offsets and valuation-staleness durations serialize canonically as part of
    updater identity, so the migrated graph can be constructed before the legacy timestamp repair
    is applied.

Before the first live execution:

- project revision `0014` must be applied for calendar-event Rebalance Configuration fields and
  bounded valuation alignment
- the installed `ms-markets` release must be at least `1.0.13`, with its provider migrated through
  revision `0016`
- built-in ms-markets Signal, Calendar, calendar-event, rebalance-state, PortfolioWeights, and
  Portfolios tables must be migrated and registered
- the selected Signal Configuration must already have at least one published observation
- the selected Bars Configuration must be enabled and its migrated raw bars table must contain the
  required assets
- the configuration-derived dynamic `InterpolatedPrices` storage must be migrated and registered
- the dedicated Job image must be ready

Normal portfolio runtime code never creates schema.

## Verification

After a successful live JobRun, verify:

- the JobRun resolved the configuration through its owning Job without command arguments
- the persistent `InterpolatedPrices` table contains the selected assets
- `PortfolioCalendarEvents` contains the configured actual session events
- `PortfolioRebalanceStateStorage` and `PortfolioWeightsStorage` use those event timestamps rather
  than midnight-normalized dates
- the Portfolio Configuration stores the resulting canonical `Portfolio.uid`
- `PortfolioWeightsStorage` and `PortfoliosStorage` contain output for that Portfolio
- the Job remains the only source for schedule, compute, image, and automatic-deployment state
