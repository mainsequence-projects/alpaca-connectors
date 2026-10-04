# ADR: Run Signal And Portfolio Configurations On Workflow-Declared Jobs

## Status

Accepted on 2026-10-05. It applies the design Jose approved for binance-connectors (its ADR 0018).
It amends the one-Job-per-configuration decisions of
[ADR 0007](0007_one_signal_configuration_per_job.md) and
[ADR 0008](0008_portfolio_configuration_and_job_ownership.md), and the description of API-created
Jobs in [ADR 0012](0012_deployment_workflow_migrations_and_schema_gated_jobs.md). The hosted checks
under [Verification](#verification) are still open.

## Context

ADR 0007 gave each signal configuration, and ADR 0008 each portfolio configuration, a dedicated Main
Sequence Job. `signal_jobs.py` and `portfolio_jobs.py` in `src/alpaca_connectors/operations/`
created it with `PlatformJob.create(...)`, wrote its schedule and compute with a Job `PATCH`
(`create_schedule`, `schedule`), and stored its UID in the configuration's `job_uid`. A run found
its configuration through `JOB_RUN_UID -> JobRun.job_uid -> configuration.job_uid`.

Since `mainsequence` 9.0.2, `Job.COLLECTION_CREATE_SUPPORTED` is `False` and `Job.create` raises
`NotImplementedError("Job creation is owned by repository workflow declarations.")`. This repository
pins `mainsequence>=9.0.5`, so the code broke:

- `POST /v1/signal-jobs` stored the row and then answered 503. The row stayed in `error` without a
  Job, and a second signal for the same Universe was refused because the Universe already had one.
- `POST /v1/portfolio-configurations` stored the row and answered 503, leaving it without a Job.
- Reconciling or updating a signal that had no Job failed the same way.

The backend agrees. These rules shape the replacement (tdag-django `01f475a86`,
`docs/tdag/pod_manager/adr/`):

- **No runtime Job creation of any kind.** `POST /api/v1/jobs/` answers 405 to every caller
  (ADR-070). Only `.mainsequence/workflows/*.yaml` declarations create Jobs.
  - A declaration owns its Job: a push deletes it when the declaration goes away (ADR-077).
  - A Job created at runtime has no binding, so no push ever deletes it.
- **A push re-applies every field the declaration states**, `task_schedule` and
  `scheduled_command_args` included. A Job has one schedule and one `scheduled_command_args` list.
  A Job `PATCH` rejects `automatic_deployment` with 400.
- **`run-job`:**
  - `command_args` are stored on `JobRun.command_args`. The backend appends them to the runtime
    command, and the runtime passes them to the script as `sys.argv[1:]`.
  - JobRuns cannot be filtered by their arguments: the SDK filters on `job__uid` and `uid` only, and
    the list comes newest first, 100 per page.
  - `run-job` answers 409 while the image built after a push is being prepared.
  - Runs of one Job may overlap.
- **The SDK finds a Job only by name**, scoped to the current branch. There is no lookup by
  workflow key.

The declared `Alpaca Bars Update` Job already works this way: the API starts it with
`--configuration-uid <uid>`. A portfolio's economic timing is already configuration data: the
rebalance configuration picks the calendar event (ADR 0009). A Job schedule only decides how often
the work runs.

The signal table, created by revision 0009, persists schedule, compute and lifecycle columns, which
ADR 0008 called duplication to remove, and `enabled`: "whether the Job schedule and manual Run
action are enabled". Pausing a signal set `enabled` to false, and that detached its Job's schedule.
So `enabled` already means "include this configuration in the scheduled runs".

## Decision

### 1. Two declared Jobs

`.mainsequence/workflows/alpaca-connectors-api.yaml` declares one Job per configuration kind,
outside `execution.steps`:

| Key | Name | Execution path | Daily crontab (UTC) |
|---|---|---|---|
| `alpaca-etf-signal` | `Alpaca ETF Signal` | `src/jobs/run_alpaca_etf_signal.py` | `30 0 * * *` |
| `alpaca-etf-portfolio` | `Alpaca ETF Portfolio` | `src/jobs/run_alpaca_etf_portfolio.py` | `0 2 * * *` |

Both Jobs declare:

- `scheduled_command_args: ["--scheduled"]`;
- 0.25 CPU, 0.5 GiB and 3600 seconds with `spot: false`, the former per-configuration API defaults;
- `automatic_redeployment.enabled: true` with `tag_regex: null`, so every push promotes the code
  that the API calls.

The times follow the 00:00 UTC daily bars Job in `daily-stock-bars-holdings-ivv.yaml`. US equity
sessions close at 20:00 UTC in summer and 21:00 UTC in winter, so the 00:30 UTC signal run follows
the close all year, and the 02:00 UTC portfolio run follows both the bars and the signal. Both run
every day, like the bars Job. On the Sunday and Monday 00:30 UTC runs no session has closed since
the last run: the signal records unchanged holdings again, and the portfolio finds no new bars.

### 2. Entry points

Each entry point runs the ADR 0012 schema gate first and then takes exactly one mode. Neither reads
`JOB_RUN_UID` to find its configuration.

- `--configuration-uid <uid>` executes that configuration. A signal runs one Universe update, and a
  portfolio bootstraps the portfolio runtime and runs its graph.
- `--scheduled` is the scheduled run. It starts one `--configuration-uid` run for each configuration
  whose scheduled flag is set.
  - It skips a configuration that has a pending or running run, and a portfolio whose preflight is
    blocked.
  - A failed start does not stop the other starts, but the scheduled run then exits 1.

### 3. Starting runs

The API and the CLI start a run with
`job.run_job(timeout=60, command_args=["--configuration-uid", uid])` on the kind's Job
(`src/alpaca_connectors/operations/configuration_jobs.py`).

- They find the Job by name and check its execution path and that its image is ready.
- They refuse while the configuration's newest run is pending or running.
- A signal run still requires an active Universe and Account, as before.
- A portfolio run needs an allowed preflight. `preflight_portfolio_configuration` resolves the stored
  inputs read-only with `resolve_portfolio_configuration`. These block the run:
  - a missing or disabled linked configuration;
  - an unsupported rebalance strategy;
  - a signal without published observations.

  Any other error propagates.
- Run history, the latest run status and `/v1/operations/job-runs/{uid}` map a run to its
  configuration by parsing `JobRun.command_args` across the Job's 500 newest runs.

### 4. The scheduled flag

Each configuration has a `scheduled` flag that decides whether its Job's scheduled run includes it:

- **Portfolio configurations** gain a `scheduled` column in revision 0016. It is a boolean,
  `NOT NULL`, default true. Its `server_default` stays permanently, so the release still serving
  during a rollout can insert rows.
- **Signal configurations reuse `enabled`** and get no new column. The API and CLI call the field
  `scheduled`. Its former second meaning is dropped: a manual run of a paused signal now starts,
  like a manual run of a paused portfolio.

Signal `pause` and `resume` set the flag, and `PATCH` sets it for both kinds. Changing only the flag
skips the reference validation, so a configuration whose linked Universe, Account or bars
configuration became inactive can still be paused.

- **Requests.** Create and update requests that still send the retired settings get 422 naming the
  workflow file. The retired settings are the nested `job` of portfolios and the flat schedule and
  compute fields of signals. A request that sends `enabled` is told to send `scheduled`.
- **Signal `reconcile`** only ensures the signal's ms-markets Signal metadata row.
- **Delete** removes only the configuration row. Its JobRuns belong to the shared Job and remain.

### 5. Responses

Configuration responses report the kind's declared Job as `job_uid` and `job`, which carries its
schedule (including the timezone), compute, image status and automatic deployment.

- Both fields are null until a push creates the Job on the branch.
- A signal's `lifecycle_state` is computed: `provisioning` before the push, then `ready` when the
  signal is scheduled and the Job has a schedule, and `paused` otherwise.
- Signal responses drop:
  - the stored schedule and compute fields;
  - `last_error`;
  - `job_image_status`, which is now `job.image_status`;
  - `job_automatic_deployment`.
- Signal lists filter on `scheduled` instead of `enabled` and `lifecycle_state`, and order by `name`
  or `updated_at`.
- Run responses add `configuration_uid` and `command_args`.

### 6. Retired columns

The retired columns stay in the schema, so the release still serving during the rollout keeps
working:

- `job_uid` stays in both tables with its unique index. Nothing reads or writes it.
- The signal table's schedule, compute, `lifecycle_state` and `last_error` columns are never read.
  New rows fill the `NOT NULL` ones with insert-only placeholders that satisfy revision 0009's
  constraints:
  - a one-day interval;
  - 0.25 CPU, 0.5 GiB, 3600 seconds and no spot;
  - lifecycle `provisioning`.

A later revision drops these columns once no release reads them.

### 7. No runtime Jobs

No code creates a Job or writes a Job's schedule. `tests/test_configuration_jobs.py` guards this by
rejecting any `<…Job>.create(...)` or `<…job>.patch(...)` call and any use of `create_schedule` in
`src/` and `api/`.

## Consequences

- **Creating a configuration no longer touches the platform.** It succeeds before the Jobs exist.
  Runs can start once a push has created them.
- **Every configuration of a kind shares one schedule and one compute size.**
  - A configuration only chooses whether the scheduled run includes it.
  - Changing the time or the compute means editing the workflow file and pushing.
  - A schedule edited on the platform reverts at the next push.
- **History depth is shared.** A configuration's run history and latest status come from the Job's
  500 newest runs.
- **Each configuration run is its own JobRun**, with its own status, logs and arguments. The
  scheduled run belongs to no configuration, so `/v1/operations/job-runs/{uid}` returns it with
  `configuration_uid: null`.
- **The scheduled run starts runs of its own Job from inside that Job.** It uses the Job runtime's
  credentials, which act as their user within the Environment.
- **A run can start before the schema is migrated.** The Jobs are outside the graph, so a push may
  promote their image before the migration Job finishes. The schema gate turns a run in that window
  into a clean skip. The next scheduled run does the work, but a manual run in that window ends as
  succeeded without having done anything.
- **Runs of the old per-configuration Jobs no longer appear** in run history or in
  `/v1/operations/job-runs/{uid}`.
- **Old per-configuration Jobs are not removed by any push.** Configurations created with SDK 8,
  between 2026-09-04 and the move to `mainsequence` 9.0.2 on 2026-10-01, may still have a dedicated
  Job. The `main` branch on the development server has two: signal
  `0039edbc-f98a-418b-8237-e935c3a1bf3c` and portfolio `c099820c-8b63-420a-aeb6-9672ae0112cd`.
  - Such a Job keeps its schedule.
  - On its old image it still finds its configuration through `job_uid` and repeats the declared
    Job's work.
  - If a push promotes it to the new image, it runs without a mode and fails with a usage error.

  [Jobs And Scheduling](../operations/jobs.md#cleanup-of-per-configuration-jobs) lists the cleanup.
- **The static site (`alpacaconnectorssite`) has to change.** It must send `scheduled` instead of
  schedule and compute fields, and show the shared schedule read-only.
- **Backend docs are out of date.** tdag-django ADR-055 still describes one platform Job per
  connector configuration as that configuration's schedule owner. Its text predates ADR-070.

## Verification

- **Tests and lint.**
  - `uv run --offline pytest` passes 353 tests, up from 320. `tests/test_configuration_jobs.py`
    covers:
    - the Job lookup, the run arguments and the launch guards;
    - mapping runs to configurations, and which JobRuns the API treats as its own;
    - the scheduled dispatch and the entry points, including the schema gate;
    - pause and resume, response shaping, and the 422 for retired settings;
    - the schema, the placeholders against revision 0009's constraints, and the no-runtime-Job
      guard.
  - `uv run --offline ruff check` passes.
- **Workflow, offline.** The backend's own validator, compiled from the tdag-django `01f475a86`
  sources, accepts the API workflow file.
  - Both new Jobs are outside `execution.steps`, and their normalized specs keep the crontabs, UTC
    and `["--scheduled"]`.
  - The API and migration Job declarations, the execution graph and the other three files are
    unchanged.
  - `alpaca-connectors-agent.yaml` still fails with `unsupported_kind`, as before this change (ADR
    0012).
- **Workflow, on the development server.** `POST
  /api/v1/code-repository-branches/945bfddd-5f1f-4541-a87a-faea3af6271f/validate-workflow/`
  returned `valid: true` with no errors or warnings, and the same normalized specs.
- **Revision 0016 on SQLite.** The project's Alembic environment, including the MetaTables SQLite
  foreign-key check, ran on a fresh file. Rows were first inserted at 0015 the way the serving
  release inserts them.
  - Those rows were backfilled to true, and an old-style insert after 0016 got the default.
  - A signal row with the placeholders passed every constraint.
  - Autogenerate found no difference between the models and the migrated configuration tables,
    and it did report the missing column at 0015.
  - A downgrade to 0015 kept the rows, and the upgrade then ran again.
- **Revision 0016 on PostgreSQL.** It renders as
  `ALTER TABLE alpaca_connectors__etf_portfolio_configuration ADD COLUMN scheduled BOOLEAN DEFAULT
  true NOT NULL`.
- **Revision 0016 through a local MetaTables 0.1.19 API**, with its own storage directory and port.
  - `metatables --local migrations upgrade --provider alpaca_connectors.migrations:migration head`
    took a 0015 runtime to 0016. The table stayed `active` in the catalog, and rows inserted at 0015
    were backfilled.
  - The new storage code then read, filtered and paused both kinds through the API, and the schema
    gate reported no provider behind.
  - A downgrade to 0015 and a second upgrade to head kept the rows.
- **Not verified:**
  - the push that creates the two Jobs and runs the migration Job;
  - a hosted manual run, the scheduled run, and `run-job` called from inside a Job runtime;
  - that the hosted runtime passes run arguments to the script. On 2026-09-03 it did not
    ([Jobs And Scheduling](../operations/jobs.md#run-arguments)). PodDeploymentOrchestrator
    `09e5ab4` (2026-09-13) fixed this, and tdag-django `01f475a86` appends `JobRun.command_args`
    to the runtime command. A run that loses its arguments fails with a usage error and does no
    work.
