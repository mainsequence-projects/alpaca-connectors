# ADR 0020: A Daily Dispatcher For Stored Bar Configurations

## Status

Proposed on 2026-11-09 and implemented the same day. It applies the dispatcher pattern of
[ADR 0013](0013_workflow_declared_configuration_jobs.md) (decision 8) to the `alpaca-bars-update`
Job. Open: the owner's review of [decision 2](#2-enabled-is-the-scheduled-flag) and the hosted
checks under [Verification](#verification).

## Context

Nothing updates bars on a schedule. The transitional `daily-stock-bars-holdings-ivv` Job, the only
scheduled bars Job, was retired on 2026-11-09 without ever succeeding: its declaration never set
the `ALPACA_BARS_CONFIGURATION_UID` its launcher required
([Retired Daily Bars Job](../operations/jobs.md#retired-daily-bars-job)).

- **The bars Job runs one configuration per run.** `Alpaca Bars Update`
  (`.mainsequence/workflows/alpaca-bars-update.yaml`, `src/jobs/run_alpaca_bars_update.py`) takes
  `--configuration-uid <uid>`. The API's update action starts it after a read-only preflight. It
  follows main (automatic redeployment since PRs #7 and #8).
- **Portfolios need fresh bars.** The 02:00 UTC portfolio dispatcher (ADR 0013) values each
  portfolio from its linked bars configuration's table. Without a bars update the run finds no new
  bars.
- **Scheduled runs need a dispatcher.** Starting a run of a Job needs `runner` on it, and a
  workflow resource cannot declare access to itself (`self_reference`). A file's
  `access.resources` names only resources of the same file (`unknown_resource`). So the dispatcher
  must live in `alpaca-bars-update.yaml`, next to the Job it starts. Moving the Job into
  `alpaca-connectors-api.yaml` would create a new Job, because the backend identifies a workflow
  target by its file and key.
- **Bar configurations already have `enabled`**, described as "whether price updates may run the
  configuration". Three checks already require it:
  - a run of a disabled configuration fails (`build_market_data_update`);
  - the API's update action refuses one in its preflight;
  - a portfolio whose bars configuration is disabled is blocked
    (`validate_portfolio_references`, used by the portfolio preflight).
- **Only daily profiles exist.** `storage_for` accepts `1d/sip/all` and `1d/iex/raw`, so every
  stored configuration has daily bars.
- **A daily update must start after UTC midnight.** For `1d` the updater requests only bars that
  start before the current UTC midnight (`current_period_start`). The session that closes at 20:00
  UTC in summer and 21:00 UTC in winter is first included by a run that starts at 00:00 UTC.

## Decision

### 1. A bars dispatcher Job

`alpaca-bars-update.yaml` declares a second Job beside `alpaca-bars-update`:

| Key | Name | Execution path | Daily crontab (UTC) | Declared access |
|---|---|---|---|---|
| `alpaca-bars-update-dispatcher` | `Alpaca Bars Update Dispatcher` | `src/jobs/dispatch_alpaca_bars_update.py` | `0 0 * * *` | `runner` on `alpaca-bars-update`, MetaTables `reader`, MainSequenceMarkets `reader` |

- It carries `scheduled_command_args: ["--scheduled"]` and the bars Job's compute: 0.25 CPU,
  0.5 GiB, 3600 seconds, no spot.
- It follows main (`automatic_redeployment.enabled: true`, `tag_regex: null`). The file has no
  execution graph, so its launcher's schema gate (ADR 0012) covers a push that promotes it before
  the migration Job finishes.
- It declares no Secret. It never opens account keys; the runs it starts do.
- The bars Job keeps manual runs only. It has never declared a schedule, so it needs no
  `task_schedule: null`.
- The dispatcher's workload User needs `data_viewer` on `alpaca-connectors` to read the
  configurations.

### 2. `enabled` is the scheduled flag

Bar configurations get no `scheduled` column. The dispatcher includes every enabled configuration.

- **`enabled` already means this.** A scheduled update is a price update, and an enabled
  configuration is one that price updates may run.
- **Portfolios accept only enabled bars configurations, and they need fresh bars.** A separate flag
  would allow a configuration that portfolios accept but nothing updates, and those portfolios
  would silently value on stale bars.
- **Every stored configuration is daily**, so no frequency needs a different schedule.
- **Nothing else changes:** no revision, and no API, CLI, Agent tool or site change.

The cost is that disabling is the only way to leave the daily run. It also refuses manual runs and
blocks the portfolios linked to the configuration. Signals, by contrast, reuse `enabled` as
`scheduled` and a manual run of a paused signal still starts (ADR 0013, decision 4). If a
configuration must ever run only on demand, or a non-daily profile is added, a later ADR adds a
`scheduled` column (`NOT NULL`, default true), as revision 0016 did for portfolios.

### 3. The scheduled run

The dispatcher's entry point runs the schema gate and then, with `--scheduled` and nothing else,
`dispatch_scheduled_bar_updates` (`alpaca_connectors.operations.bar_updates`):

- It finds the `Alpaca Bars Update` Job the way the API does: by name, with the reviewed execution
  path and a ready image. Otherwise it fails and starts nothing.
- It reads the enabled configurations and starts one `--configuration-uid <uid>` run for each.
- It skips a configuration with a pending or running run among the Job's 500 newest runs.
- One failed start does not stop the others, but the scheduled run then exits 1.
- It prints what it launched, skipped and failed.

It runs no preflight. The API's preflight answers its caller before a run starts, and the
dispatcher has no caller. Each run resolves its configuration itself, minutes later, and a
configuration that cannot resolve fails in its own JobRun, with its own logs.

### 4. Timing

00:00 UTC is the earliest daily time at which the updater includes the session that just closed
(see Context). It precedes the 00:30 UTC signal and 02:00 UTC portfolio dispatchers. The signal
does not read bars; the portfolio does, so the bars runs have two hours. The dispatcher runs every
day, like the other dispatchers. On the Sunday and Monday 00:00 UTC runs no session has closed
since the previous run, so the updates find no new bars.

## Consequences

- **Every enabled configuration is updated daily** without anyone starting a run.
- **All configurations share one time and one compute size.** Changing them means editing the
  workflow file and pushing; a schedule edited on the platform reverts at the next push.
- **The dispatcher's runs are not bars runs.** `GET /v1/operations/job-runs/{uid}` returns only
  runs of `Alpaca Bars Update`, so it returns the runs the dispatcher starts but not the
  dispatcher's own.
- **The API is unchanged.** It keeps branch `editor` to start the bars Job, and no response
  reports the bars schedule.
- **Many runs can start at 00:00 UTC.** Runs of one Job may overlap, and each takes the bars Job's
  compute.
- **A run longer than two hours delays the portfolio's bars**, since the portfolio dispatcher does
  not wait for the bars runs.

## Verification

- **Tests and lint.** `pytest` passes 636 tests. `tests/test_bar_update_jobs.py` covers the
  dispatch, the enabled-configuration query, the entry point, the declaration and its time against
  the other dispatchers. `tests/test_schema_gate.py` covers the schema gate and the MetaTables
  access. `ruff check`, `ruff format --check` and `mkdocs build --strict` pass.
- **Workflow, offline.** The backend's own validator, `CodeRepositoryWorkflowService.validate` at
  tdag-django `3b31ebe6c` (`development`, 2026-11-08), accepts `alpaca-bars-update.yaml` and
  `alpaca-connectors-api.yaml` with no errors or warnings. Its normalized dispatcher keeps the
  `0 0 * * *` UTC crontab and `["--scheduled"]`. The same file with `runner` on
  `alpaca-etf-signal` is refused with `unknown_resource`, and with `runner` on the dispatcher
  itself with `self_reference`, which is why the dispatcher lives beside the bars Job.
- **Not verified:**
  - `validate-workflow` on a running platform: the local backend was stopped;
  - the push that creates the dispatcher;
  - its first scheduled run, and `run-job` called from inside a Job runtime, which is also open for
    the ADR 0013 dispatchers;
  - that the hosted runtime passes run arguments to the script
    ([Run arguments](../operations/jobs.md#run-arguments)).
