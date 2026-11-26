# ADR 0034: Portfolio Runs Can Run Their Updaters In Node Jobs

## Status

Proposed on 2026-11-26 by the owner. Not implemented. It builds on decision 9 of
[ADR 0013](0013_workflow_declared_configuration_jobs.md#9-one-run-per-tick) (the dispatcher's
scheduled run starts one `alpaca-portfolio` Job run for every scheduled configuration) and on
decision 6 of [ADR 0033](0033_an_accounts_portfolios_share_one_price_chain.md) (every updater
rebuilds in a node Job run). It applies MetaTables ADR 0032 and ms-markets ADR 0067 to this
application.

## Context

### How a portfolio Job run runs its updaters

A run of the `alpaca-portfolio` Job ("Alpaca Portfolio") with several `--configuration-uid` values
calls `execute_portfolio_configurations`, which calls `markets_api.run_portfolios` and then
ms-markets' `msm_portfolios.recipes.run_portfolios`. That calls MetaTables' `run_updaters` with
every portfolio's `PortfolioEngine` as a head, and MetaTables joins their dependencies into one
invocation keyed by update UID. It runs that invocation in one of two ways:

- **In process**, the default (`node_job=None`). The portfolio Job run runs every updater of the
  invocation itself, one after another. MetaTables has no threads.
- **In node Jobs** (`node_job="<Job name>"`, `max_parallel_nodes`). The portfolio Job run only
  orchestrates. It checks that every updater rebuilds (`node_arguments`, no request), opens the
  invocation, and starts one run of the node Job for each updater whose dependencies have ended,
  independent ones at the same time, up to `max_parallel_nodes`. Each node Job run rebuilds its
  updater from the saved configuration, runs it, and reports its start and end to the MetaTables
  API; the orchestrator reads the invocation's graph every 5 s and each node Job run's status every
  30 s. The weights and values are still published by the portfolio Job run, after the graph.

### What each way costs here

Measured in Production on 2026-11-23: a Job run starts in about 47 s (imports on 0.25 CPU, the
schema gate, the engine), and an updater's MetaTables bookkeeping takes about 40 to 50 s
(6 to 8 requests at 3 to 12 s each). With ADR 0033 an invocation of N portfolios of one account
holds 2N + 3 updaters, in four levels: `AccountStockBars` (with `PortfolioCalendarEvents` beside
it), its `InterpolatedPrices`, every `PortfolioRebalance`, every `PortfolioEngine`.

| Portfolios | In process: (2N + 3) × ~45 s | In node Jobs: 4 × (~47 s + ~45 s) |
|---|---|---|
| 1 | 5 updaters, ~4 min | ~6 min |
| 3 | 9 updaters, ~7 min | ~6 min |
| 10 | 23 updaters, ~17 min | ~6 min |

In process the run grows with the number of portfolios; in node Jobs it grows with the depth of
the graph. Node Jobs are faster from about three portfolios in one portfolio Job run. These are
estimates from the measured costs, not measured runs.

### What node Jobs cost besides time

- A pod per updater: 23 node Job runs for 10 portfolios, each with the platform's 90 s drain
  after it exits, which is billed. The drain does not hold up the orchestrator, which reads each
  updater's end from the invocation, not from the Job run's status.
- Each node Job run reads for itself. One runtime lookup, one read of each calendar and the rows
  passed from updater to updater (ms-markets #137, #139, #140) apply inside one process only.
- A node Job needs a hosted runtime; a local runtime refuses it.

## Decision

### 1. One node Job, declared in the API's workflow

`.mainsequence/workflows/alpaca-connectors-api.yaml` declares `alpaca-portfolio-node`:

```yaml
- key: alpaca-portfolio-node
  kind: job
  access:
    branches:
    - repository: MetaTables
      policy: reader
    - repository: MainSequenceMarkets
      policy: reader
    secrets:
    - name: ALPACA_CONNECTORS_DATA_KEY
      policy: value_reader
  spec:
    name: Alpaca Portfolio Node
    description: Run one updater of a portfolio Job run's invocation (started by that run).
    execution_path: src/jobs/run_updater_node.py
    cpu_request: '1'
    memory_request: '1'
    max_runtime_seconds: 3600
    spot: false
    task_schedule: null
    scheduled_command_args: []
    automatic_redeployment:
      enabled: true
      tag_regex: null
```

- **No schedule and no step.** Only a portfolio Job run starts it. No step references it, so a
  push promotes its image without waiting for the migration, as for the portfolio Job; its
  launcher's schema gate covers that gap (ADR 0012).
- **Its access is the portfolio Job's.** An updater reaches, when it runs, what the portfolio Job
  reaches: the MetaTables API, the Markets API, and the account keys, which `AccountStockBars`
  resolves itself (ADR 0033, decision 2). The node Job's workload gets `editor` on this
  application's namespace from `settings.BRANCH_ACCESS`, as every workload of the branch does
  (ADR 0030).
- **Its size is the portfolio Job's** until a measured run says otherwise. No updater declares
  `node_resources()`, so the portfolio Job needs `runner` on it, not `sized_runner`.

### 2. Its launcher

`src/jobs/run_updater_node.py` runs `skip_if_schema_behind("Alpaca portfolio node Job")` first, as
every launcher except the migration Job does, then `msm_portfolios.node_job.run_node()`.
`run_node` runs MetaTables' `run_updater_node()` inside the per-run scopes ms-markets gives its
nodes, with the arguments the orchestrator passed (`--update-uid`, `--invocation-uid`,
`--trace-id`, `--output-table`, `--argument-table`). A node Job run whose schema is behind does no
work and never reports its updater's end, so the orchestrator fails that updater ("ended before
the node reported its end") and everything that needs it.

### 3. The portfolio Job may start it

The `alpaca-portfolio` declaration adds:

```yaml
    resources:
    - key: alpaca-portfolio-node
      policy: runner
```

### 4. Which runs use it

- **The portfolio Job's environment selects it.** The `alpaca-portfolio` spec sets
  `ALPACA_PORTFOLIO_NODE_JOB: Alpaca Portfolio Node`, and optionally
  `ALPACA_PORTFOLIO_MAX_PARALLEL_NODES` (default `-1`, ms-markets' default: no limit). A portfolio
  Job run with several configurations passes both to `run_portfolios` when the first is set.
- **A run of one configuration stays in process.** The API's run action and the CLI start one; it
  holds 5 updaters, which run faster in one process.
- **Local runs never set it.** The local stack and the lifecycle check run against a local runtime,
  which refuses node Jobs, so they always run in process. The selection lives only in the hosted
  workflow, so both ways run the same code.
- **The code supports both ways from the start.** Every updater follows the rules in
  [Development](../operations/development.md#updaters-run-in-node-jobs), and `AGENTS.md` states
  them for every updater this repository writes.

### 5. It is adopted by measurement

The first hosted scheduled run of three or more portfolios runs in node Jobs; the next, with the
variable removed, in process. The faster is kept, and this ADR records both durations.

## Consequences

- **Run history.** Node Job runs carry `--update-uid`, never `--configuration-uid`, so a
  configuration's run history and latest run still come from the portfolio Job alone
  (ADR 0013). A node Job run's logs are in its own run; the portfolio Job run's log names each
  node Job run it started.
- **A node that cannot be rebuilt fails the whole portfolio Job run before anything starts**:
  every portfolio fails with that error. The rebuild tests (Development, "Tested as a node") are
  what catch it first.
- **The portfolio Job run waits.** Its `max_runtime_seconds` (3600) bounds the whole invocation,
  node Job runs included.
- **Billing.** Up to 2N + 3 pods per scheduled run, each with its 90 s drain.
- **Nothing changes in process.** Without the variable, a run is what it is today.

## Not Decided Here

- Node sizes (`node_resources()`) and `sized_runner`.
- Choosing the mode by the number of portfolios in a run, instead of by the variable.
- Node Jobs for a run of one configuration.

## Verification

- Unit tests: the launcher runs the schema gate before `run_node`; a portfolio Job run with several
  configurations passes `node_job` and `max_parallel_nodes` only when the variable is set; a run of
  one configuration never passes them; the workflow declares the node Job with the access above and
  gives the portfolio Job `runner` on it.
- ms-markets' `node_arguments` and MetaTables' `rebuild_updater` on every updater of a portfolio Job
  run's invocation (ADR 0033, Verification).
- Hosted: `validate-workflow` on the file; the two scheduled runs of decision 5, with their
  durations and the node Job runs they started.
