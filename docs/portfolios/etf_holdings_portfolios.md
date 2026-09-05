# ETF Holdings Portfolios

## Goal

Build a durable ms-markets analytical portfolio from an existing ETF Weight Signal, an existing
Alpaca Bars Configuration, persistent interpolated prices, and a reusable Rebalance Configuration.
See [ADR 0008](../adrs/0008_portfolio_configuration_and_job_ownership.md) for the ownership and
backtest decision.

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
- valuation column and missing-price policy
- portfolio output frequency and commission-fee assumption
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

Rebalance Configuration ────────────> ImmediateSignal

ETF Weight Signal + InterpolatedPrices + ImmediateSignal
                                  └──> PortfoliosDataNode
                                       ├──> PortfolioWeightsStorage
                                       └──> PortfoliosStorage
```

The portfolio Job does not re-extract the ETF Universe and does not run the bars updater as a
dependency. It requires an existing Signal observation, updates only the persistent interpolation
node, then runs `PortfoliosDataNode` with `update_tree=False`. Signal and raw bars producers remain
separate Jobs with their own schedules and execution histories.

## Phase-1 Backtest Semantics

Phase 1 supports only `ImmediateSignal`. At every available signal observation, the analytical
backtest assumes the portfolio immediately adopts those weights. It does not model execution
latency, partial fills, volume participation, market impact, or slippage beyond the configured
commission fee.

The ETF Weight Signal timestamp records when the connector observed the provider holdings. It does
not guarantee the weights became economically effective at that exact instant. The resulting
portfolio is therefore an observation-time reconstruction, not a perfect point-in-time ETF
replication.

## CLI

Create the reusable rebalance policy once:

```bash
alpaca-connectors portfolio rebalance create \
  --name "Immediate observed weights" \
  --strategy immediate_signal
```

Create a Portfolio Configuration and its dedicated Job together:

```bash
alpaca-connectors portfolio create \
  --name "Daily IVV analytical portfolio" \
  --signal-configuration-uid <SIGNAL_CONFIGURATION_UID> \
  --bars-configuration-uid <BARS_CONFIGURATION_UID> \
  --rebalance-configuration-uid <REBALANCE_CONFIGURATION_UID> \
  --schedule-type crontab \
  --schedule-expression "30 8 * * 1-5" \
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

## Required Platform State

Before the first live execution:

- revision `0010` must be applied for the Portfolio and Rebalance Configuration tables
- built-in ms-markets Signal, Calendar, Portfolio, PortfolioWeights, and Portfolios tables must be
  migrated and registered
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
- the Portfolio Configuration stores the resulting canonical `Portfolio.uid`
- `PortfolioWeightsStorage` and `PortfoliosStorage` contain output for that Portfolio
- the Job remains the only source for schedule, compute, image, and automatic-deployment state
