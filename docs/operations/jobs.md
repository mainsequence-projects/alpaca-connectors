# Jobs And Deployment

## The Portfolio Job

Portfolio configurations run on one Job declared in
`.mainsequence/workflows/alpaca-connectors-api.yaml`, and a dispatcher Job in the same file starts
its scheduled runs ([ADR 0013](../adrs/0013_workflow_declared_configuration_jobs.md),
[ADR 0027](../adrs/0027_one_portfolio_path.md)). No code creates a Job: since `mainsequence`
9.0.2 only a workflow declaration can, and the push that carries a declaration creates its Job.

| Key | Display name | Daily schedule | Arguments |
|---|---|---|---|
| `alpaca-portfolio` | `Alpaca Portfolio` | none | `--configuration-uid <uid>` per run |
| `alpaca-portfolio-dispatcher` | `Alpaca Portfolio Dispatcher` | `0 2 * * *` UTC | `--scheduled` |

The launchers are `src/jobs/run_alpaca_portfolio.py` and `src/jobs/dispatch_alpaca_portfolio.py`.
The dispatcher runs after the US equity close and after the 00:00 UTC
[bars dispatcher](#daily-bars-dispatcher). Both Jobs use 0.25 CPU, 0.5 GiB and 3600 seconds, and
every push promotes their image. No `execution.steps` entry references them, so a push may promote
their image before the migration Job finishes; the schema gate in each entry point turns a run in
that window into a clean skip
([ADR 0012](../adrs/0012_deployment_workflow_migrations_and_schema_gated_jobs.md)). The gate is
also a run's first call to the Markets and MetaTables APIs, which are usually asleep at these hours.
It waits up to 300 s for each to wake. ms-markets' own 30 s failed every dispatcher run from
2026-11-09 to 2026-11-13
([ADR 0020](../adrs/0020_dependencies_that_are_starting.md), decision 5).

How they run:

- **A manual run executes one configuration.** `POST /v1/portfolio-configurations/{uid}/actions/run`
  and `alpaca-connectors portfolio run <uid>` start a run of the Job with the arguments
  `--configuration-uid <uid>`. A run is refused while the configuration's newest run is pending or
  running, or while the Job's image is not ready, which includes the image build after each push.
  It also needs an allowed preflight: its bars configuration must exist and be enabled, its
  rebalance configuration must exist, and the portfolio must have at least one published
  observation.
- **The scheduled run belongs to the dispatcher and executes nothing itself.** The dispatcher
  starts one run of the portfolio Job for the whole tick, carrying `--configuration-uid` once for
  each configuration whose scheduled flag is true ([ADR 0013, decision
  9](../adrs/0013_workflow_declared_configuration_jobs.md#9-one-run-per-tick)). It may, because it
  declares `runner` on that Job. That run resolves the configurations together, updates each bars
  configuration once, and runs every portfolio in one ms-markets `run_portfolios` call: one start
  and one drain for the tick, and no run waits for another's update. The portfolio Job cannot start its
  own runs: starting a run needs `runner` on the Job, and a Job cannot declare access to itself
  (`self_reference`).
  - It skips configurations with a pending or running run, and those whose preflight is blocked.
    The preflights of all the others read their inputs together, one read per kind, from the
    scheduled rows already read (issue #46).
  - It prints what it launched, skipped and failed, and fails if the start failed.
  - The tick's run prints each portfolio it ran and each that failed, and fails if any failed, so
    each scheduled configuration's latest run is the tick's.
  - The portfolio Job accepts only `--configuration-uid`; a run with `--scheduled` fails with a
    usage error.
- **`scheduled` is the only per-configuration operational setting**, and it defaults to true.
  `PATCH` sets it, and so do the CLI's `--scheduled` and `--no-scheduled`. A configuration left out
  of the scheduled run can still be run manually.
- **Schedule and compute belong to the declarations.** To change them, edit the workflow file and
  push. A push re-applies the declared schedule, so a schedule edited on the platform lasts only
  until the next push. A push changes only the fields a declaration states, so the portfolio Job
  declares `task_schedule: null` and empty `scheduled_command_args`. Requests that still send
  schedule or compute settings get 422.
- **Run history comes from the Job's 500 newest runs.** Configuration responses report the Job as
  `job_uid` and `job`, with its dispatcher's schedule. Both are null until the push creates the
  Job, and the schedule is null while the dispatcher is missing. Run lists and
  `/v1/operations/job-runs/{uid}` find a configuration's runs by their `--configuration-uid`
  argument. Dispatcher runs are not configuration runs, so that route does not return them.

A dispatcher starts runs of another Job from inside a Job runtime, with its own workload's
`runner` policy. Confirm it on the first scheduled runs after a push that creates a dispatcher
(00:00 UTC for bars, 02:00 UTC for portfolios). A new Job's workload User gets its MetaTables
role through the branch on its first request
([Table Access And MetaTables Roles](#table-access-and-metatables-roles)). Before
[ADR 0030](../adrs/0030_workloads_get_their_roles_through_their_branch.md), the rename of
[ADR 0027](../adrs/0027_one_portfolio_path.md) created both Jobs anew with no role, and their runs
skipped (issue #44).

### Cleanup Of Per-Configuration Jobs

Before ADR 0013, the API created one Job per configuration. That worked with SDK 8, from 2026-09-04
until the repository moved to `mainsequence` 9.0.2 on 2026-10-01. Such a Job has no workflow
binding, so no push deletes it, and it keeps its schedule and runs its launcher by path.
[ADR 0027](../adrs/0027_one_portfolio_path.md#r3-drop) retires those launchers and names these
Jobs, so the Jobs must be gone before the launcher files are.

On each platform branch:

1. List the candidates from a checkout of the branch with the `jobs list` filter of
   [ADR 0027](../adrs/0027_one_portfolio_path.md#r3-drop). On 2026-10-05 the `main` branch on the
   development server had two: `0039edbc-f98a-418b-8237-e935c3a1bf3c` (signal) and
   `c099820c-8b63-420a-aeb6-9672ae0112cd` (portfolio).
2. Delete each per-configuration Job in the platform UI, or with
   `uv run python -c "from mainsequence.client import Job; Job.destroy_by_uid('<Job UID>')"`.
   The `mainsequence` 9.0.5 CLI has no Job delete command. Deleting a Job also deletes its JobRuns.
3. Check that the list from step 1 is empty.

### Cleanup Of The Duplicate Migration Job

On 2026-10-05 the `main` branch on the development server had two Jobs named
`Alpaca Connectors Migrations`. Only one is bound to the current declaration:

| Job | Workflow binding (file, key) | Created by the push of | Runs |
|---|---|---|---|
| `f6e2433e-fe65-49ef-af83-6f515c26b023`, current | `.mainsequence/workflows/alpaca-connectors-api.yaml`, `alpaca-connectors-migrate` | `c98c629`, 2026-10-03 17:40 UTC | `216e21f3`, failed with a SQLAlchemy `ProgrammingError` |
| `ed550d08-226f-42e5-8db1-9f00eea3d30a`, stale | `.mainsequence/workflows/alpaca-connectors-migrations.yaml`, `alpaca-connectors-migrate` | `48d135d`, 2026-10-03 16:28 UTC | `eb9dac64`, failed (`backofflimitexceeded`) |

The backend identifies a workflow target by its branch, workflow file path and resource key
(tdag-django ADR-030), so moving a declaration to another file creates a new target. `48d135d`
declared the migration Job in its own file, `alpaca-connectors-migrations.yaml`. `c98c629` moved
the same key into `alpaca-connectors-api.yaml` (ADR 0012) and removed that file, which created
`f6e2433e`. At the time the backend kept targets whose declaration was gone, so `ed550d08` stayed.
Its image stays at `48d135d`, it has no schedule, and no execution step references it, so nothing
runs it again.

The next push of `main` deletes it. Since tdag-django ADR-077 (`4a2833f12`, part of the
`development` code the development server runs), a push to the branch head deletes every
workflow-bound Job whose file and key no valid file of the head declares, after applying the push's
own declarations. `alpaca-connectors-migrations.yaml` no longer exists, so:

- the backend's read-only ADR-077 report (`report_undeclared_workflow_targets` for this branch, at
  head `c98c629`) lists exactly `ed550d08` as undeclared;
- Django's deletion collector, run without deleting anything, finds no protected or restricted
  rows. The deletion also removes its JobRun `eb9dac64`, its pod record, redeployment policy and
  runtime session. Its image deployment run keeps its record, with the Job reference cleared;
- the commits after `c98c629` only add the two configuration Jobs to `alpaca-connectors-api.yaml`,
  so nothing else becomes undeclared. The invalid `alpaca-connectors-agent.yaml` protects only
  targets bound to its own path. The per-configuration Jobs above have no binding and are not
  affected.

After the push:

1. Read the workflow action result of the pushed commit. Its `deletions` list should report
   `ed550d08-226f-42e5-8db1-9f00eea3d30a` as `deleted`. If it reports `deletion_blocked`, its
   reason says why and the next push tries again. Delete the Job by hand only in that case, as in
   step 2 of the cleanup above.
2. `mainsequence code-repository jobs list --filter "name__contains=Alpaca Connectors Migrations"`
   should list only `f6e2433e-fe65-49ef-af83-6f515c26b023`.

The API deploy step needs the migration step, and the current Job's only run failed. Check that the
push's migration run succeeds before expecting a new API revision.

## Portfolio Runs

A portfolio configuration stores only calculation inputs and its scheduled flag. Schedule,
compute, spot, maximum runtime, automatic deployment, and image state belong to the declared
portfolio Job. An application creates the configuration and gives it weights over HTTP
([Portfolios](../portfolios/portfolios.md)), and its owner pauses and resumes it with
`PATCH /v1/portfolio-configurations/{uid}` and `{"scheduled": false}` or `true`. The CLI runs
and inspects it:

```bash
alpaca-connectors portfolio run <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio runs <PORTFOLIO_CONFIGURATION_UID>
```

The inputs the run resolves are read in three steps, each step's reads together. It then reads
the portfolio's published observations and runs the ms-markets recipe `run_portfolio`: one
MetaTables invocation runs its account's `AccountStockBars` and `InterpolatedPrices` on its bars
profile ([ADR 0033](../adrs/0033_an_accounts_portfolios_share_one_price_chain.md)), the calendar
events, the rebalance state and the ledger (`PortfolioEngine`, ms-markets ADR 0065), and the run
then publishes the weights and values the ledger projects. The bars node reads the bars table
first: when every asset already holds the bar of the last session whose daily bar is final (its
New York midnight before today's 00:00 UTC), it fetches nothing and resolves no keys. A failed
fetch fails the run. In a run of several configurations, every portfolio of one account and
profile shares the bars and prices nodes, and every portfolio of a calendar its events.
The interpolated prices are written to the table of the portfolio's bars profile, which revision
`0034` creates and the migration Job registers
([ADR 0029](../adrs/0029_interpolated_price_tables_are_named_like_every_project_table.md)); the
Job needs nothing prepared. See
[ADR 0008](../adrs/0008_portfolio_configuration_and_job_ownership.md).

A run's summary carries `first_rebalance`: `rebalanced` is true only when Markets holds the
portfolio's weights. A run whose first rebalance was due and recorded no weights says so in its
`message` and log, instead of reporting the schedule.

## Alpaca Bars Update Job

The canonical market-data Job is declared by:

- `src/jobs/run_alpaca_bars_update.py`;
- `.mainsequence/workflows/alpaca-bars-update.yaml`.

The Job is named `Alpaca Bars Update`. It is generic: each run supplies only the public UID of an
enabled stored bars configuration. The API starts it on demand, and the
[bars dispatcher](#daily-bars-dispatcher) in the same file starts it every day for each enabled,
scheduled configuration.

```bash
mainsequence code-repository jobs run <JOB_UID> -- \
  --configuration-uid <CONFIGURATION_UID>
```

The launcher resolves the stored configuration at run time and always executes the update. It does
not accept a dataset UID, account override, asset-source override, bar-profile override, Secret
name, credential value, hash namespace, or removed `force_update` option. Main Sequence records the
two command arguments on the resulting JobRun.

The workflow uses workflow API `3.0.0`. The Job has no schedule and no configuration environment
variable; the dispatcher holds the schedule. Automatic redeployment is enabled (PRs #7 and #8,
2026-11-04), so every push promotes this Job to the new image, as it does the API's Jobs. The file
has no `execution.steps`, so a push may promote it before the migration Job finishes; its
launcher's schema gate turns a run in that window into a clean skip
([ADR 0012](../adrs/0012_deployment_workflow_migrations_and_schema_gated_jobs.md)).

The API starts this Job through its branch `editor` policy. A workflow file's `access.resources`
can name only resources of the same file (`unknown_resource`), so the API cannot declare `runner`
on this Job alone while the Job lives in `alpaca-bars-update.yaml`. Moving the declaration into
`alpaca-connectors-api.yaml` would create a new Job, because the backend identifies a workflow
target by its file and key ([duplicate migration Job](#cleanup-of-the-duplicate-migration-job)).

### Daily Bars Dispatcher

`alpaca-bars-update.yaml` also declares `alpaca-bars-update-dispatcher`
([ADR 0021](../adrs/0021_daily_bars_dispatcher.md)), the bars counterpart of the portfolio
dispatcher:

| Key | Display name | Daily schedule | Arguments |
|---|---|---|---|
| `alpaca-bars-update` | `Alpaca Bars Update` | none | `--configuration-uid <uid>` per run |
| `alpaca-bars-update-dispatcher` | `Alpaca Bars Update Dispatcher` | `0 0 * * *` UTC | `--scheduled` |

- **It starts the daily runs and updates nothing itself.** Its scheduled run
  (`src/jobs/dispatch_alpaca_bars_update.py`) starts one `--configuration-uid` run of
  `Alpaca Bars Update` for each enabled, scheduled bar configuration, through the `runner` policy
  it declares on that Job. It must live in this file, because `access.resources` names only
  resources of the same file.
  - It skips a configuration whose run is pending or running among the Job's 500 newest runs.
  - It runs no preflight: each run resolves its own configuration and records its own failure.
  - It prints what it launched, skipped and failed, and fails if any start failed.
- **`scheduled` decides the daily run; `enabled` decides whether any run may start.** A
  configuration is updated daily only when both are true (revision 0024; `scheduled` defaults to
  true). To leave the daily run but keep on-demand updates, unschedule it
  (`alpaca-connectors market-data bar-configuration update <uid> --no-scheduled`, or `PATCH` with
  `scheduled: false`). Disabling it (`--no-enabled`) also refuses its on-demand runs and blocks the
  portfolios linked to it. A portfolio may use an unscheduled configuration; its valuation
  staleness settings decide what it does with bars that were not updated.
- **00:00 UTC is the earliest daily time that includes the session just closed.** The `1d` updater
  takes only bars that start before the current UTC midnight, and US sessions close at 20:00 UTC
  in summer and 21:00 UTC in winter. The run precedes the 02:00 UTC portfolio dispatcher, so the
  bars runs have two hours before the portfolio runs read them. The Sunday and Monday 00:00 UTC
  runs find no new session.
- **Its compute matches the bars Job's** (0.25 CPU, 0.5 GiB, 3600 seconds, no spot), and every
  push promotes it. It declares no Secret: it never opens account keys, the runs it starts do.
- **`GET /v1/operations/job-runs/{uid}` does not return the dispatcher's runs,** only the
  `Alpaca Bars Update` runs it starts.

Its workload User holds `editor` on `alpaca-connectors` through the branch
([Table Access And MetaTables Roles](#table-access-and-metatables-roles)).

### Run arguments

The bars Job and the portfolio Job receive their per-run arguments through
`JobRun.command_args`. Live verification on 2026-09-03 showed the runtime wrapper dropping them: run
`d060f538-d63a-4328-bce9-979643876033` recorded the two arguments but invoked the launcher without
them, so its required-argument validation failed.

PodDeploymentOrchestrator `09e5ab4` (2026-09-13, "preserve script child argv") passes them to the
script as `sys.argv[1:]`, and tdag-django `01f475a86` appends `JobRun.command_args` to the runtime
command. No hosted run has confirmed the fix yet. A run that loses its arguments fails with a usage
error and does no work. Do not weaken a launcher or move a configuration into an environment
variable to work around a missing argument; repeat the smoke test with a real configuration
instead.

## API Invocation And Observation

The FastAPI update action performs a read-only resolution preflight and submits the canonical Job:

```text
POST /v1/market-data/bar-configurations/{configuration_uid}/actions/update
GET  /v1/operations/job-runs/{job_run_uid}
```

The POST returns `202 Accepted`; it does not perform Alpaca or MetaTable writes in the HTTP process.
The GET returns normalized JobRun state, timestamps, frozen commit and image identity, command
arguments, and the platform application-log URL. Poll responses use `Cache-Control: no-store`, and
provider exception details are not exposed.

## Retired Daily Bars Job

`daily-stock-bars-holdings-ivv` (display name `Alpaca Stored Bar Configuration`) was a scheduled
00:00 UTC Job that ran one stored bars configuration named by the `ALPACA_BARS_CONFIGURATION_UID`
environment variable. It was retired on 2026-11-09 (issue #11), and its declaration
`.mainsequence/workflows/daily-stock-bars-holdings-ivv.yaml` and launcher
`src/jobs/run_daily_stock_bars_holdings_ivv.py` were removed.

- **It never worked.** The declaration set no `ALPACA_BARS_CONFIGURATION_UID`, so every run
  failed before the schema gate with "ALPACA_BARS_CONFIGURATION_UID must identify an enabled
  stored bar configuration." On 2026-11-09 the Production Job was still pinned to the image of
  `7da1451` (2026-10-11), with automatic redeployment disabled, and none of its 28 runs had
  succeeded. Retiring it removed no working update.
- **The push to main that removed the declaration deletes the platform Job,** its schedule and its
  JobRuns, under the current workflow contract. Check the repository event's deletion result.
- **Its workload User's roles can go.** It held `data_viewer` on `alpaca-connectors` and table
  `editor` on the two bars tables; the namespace owner removes them.
- **The [bars dispatcher](#daily-bars-dispatcher) replaced it**
  ([ADR 0021](../adrs/0021_daily_bars_dispatcher.md)). It updates every enabled, scheduled
  configuration at 00:00 UTC through `Alpaca Bars Update`. Until it was declared, nothing updated bars on a
  schedule.

## Lifecycle Check Job

**Not declared for now.** The check runs only from a person's machine
([ADR 0032](../adrs/0032_the_lifecycle_check_runs_as_an_on_demand_job.md), status). Its launcher
and code stay, and this section is what the declaration is when it comes back.

`alpaca-lifecycle-check` (display name `Alpaca Lifecycle Check`) runs the account and portfolio
life cycle check inside the platform, on demand. It is declared in `alpaca-connectors-api.yaml`
with no schedule and is no step of the execution graph, so a push never runs it. Its launcher is
`src/jobs/run_lifecycle_check.py`, which starts with the schema gate.

| Access | Why |
| --- | --- |
| `user` on `alpaca` | It calls this branch's API as its own workload, as any caller does |
| `reader` on the MetaTables and MainSequenceMarkets branches | It reaches Markets through the Markets API, as every Job does |
| `value_reader` on `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY` | The paper account's keys, sent only to the account create route and to Alpaca |

It holds no `runner`: the API starts stage 10's portfolio Job runs. How to run it and read its
report: [Development Workflow](development.md#running-it-in-the-platform).

## FastAPI Release

`.mainsequence/workflows/alpaca-connectors-api.yaml` declares the registered FastAPI resource with
automatic deployment, revision retention, and the platform-provided static-site origin policy.
A valid workflow or successful Git push is not proof of deployment; verify the ResourceRelease and
its DeploymentRun separately.

## Table Access And MetaTables Roles

Every table this project owns is registered in its own `alpaca-connectors` namespace (ms-markets
ADR 0047, decision 4): the project tables, its Alembic version registry, and its instances of the
Markets time-series contracts (signal weights, portfolio values, weights, rebalance state,
calendar events, orders, trades and interpolated prices; `alpaca_connectors.instances`).
Environments that ran earlier releases move the thirteen older project tables there once; see
the [Markets API Boundary Runbook](markets-api-boundary.md). No workload holds a role on
`mainsequence.markets`: Markets registries (assets, asset types, OpenFIGI details, accounts,
holdings, asset snapshots, categories and memberships, calendars, portfolios, signal metadata,
order managers and the portfolio dataset catalog) are read and written through the Markets API
(see Markets API Access below). The migration namespace and the table's catalog namespace are
separate concepts; inspect the registered table rather than inferring its grant scope from the
provider name.

Since MetaTables 0.1.46 (its ADR 0024) access is a role on a namespace or a table. `data_viewer`
reads rows, `data_writer` also writes rows, `editor` also registers and changes tables and runs
updaters, and `owner` also decides who has access. A namespace role covers the namespace's tables.
A person, or a Team of people, owns each namespace; a workload never owns one, never grants
through the access routes, and never creates a table outside a namespace.

In each Environment where the project runs:

1. ms-markets must already be deployed and migrated there by its own deployment, which owns the
   `mainsequence.markets` schema, and its Markets API must serve the release this image runs
   (the Jobs' schema gate checks its `/health`). The project's revisions reference its tables by
   physical name.
2. The migration Job gives every workload of this repository's branch `editor` on
   `alpaca-connectors`
   ([ADR 0030](../adrs/0030_workloads_get_their_roles_through_their_branch.md), MetaTables
   ADR 0029). The provider declares `settings.BRANCH_ACCESS` after each finalization, on every
   push. The API, the Agent, the migration Job, the bars and portfolio Jobs and both dispatchers
   have the role on their first request. A renamed or recreated workload keeps it, and nobody
   grants a workload User by hand. In a new Environment the migration Job creates the namespace
   and holds `editor` on it. A refused declaration fails the Job with `BranchAccessRefused`.

   Markets holds no role on `alpaca-connectors` or any of its tables: this application publishes
   to Markets and reads it back through the Markets API
   ([ADR 0031](../adrs/0031_markets_series_are_published_not_shared.md)), and never a role on
   `alpaca_connectors__acct_alpaca`, whose columns hold the application-encrypted keys.

   A person still grants each namespace an owner, once per Environment.
3. Every one of those resources except the Agent declares
   `access.branches: [{repository: MetaTables, policy: reader}]` in its workflow file. A migration
   that fails with `namespace_access_required`, `namespace_not_writable` or `table_not_editable`
   (403) names the `caller_user_uid` that lacks a role: the owner grants it `editor`, after which
   its branch declaration covers its replacements. A manual run of the
   migration Job then applies the revisions, but only another push deploys the API that waited for
   them. Do not run the Job as an Organization admin to get around a refusal.

An Organization admin's ability to administer the platform does not by itself give a workload
table access.

## Markets API Access

The connector reaches Markets through the Markets API (ms-markets ADR 0047): every entrypoint
selects ms-markets' remote row backend (`alpaca_connectors.runtime.use_markets_api`), attaches only
its own `alpaca-connectors` tables, and reads and writes Markets registries with `msm.api` row
calls, `AccountHoldingsSnapshots`, `AssetSnapshots` and the ms-markets recipes that publish and
read signal weights, portfolio weights and portfolio values (ADR 0031). In each Environment
the workflows grant every resource that calls Markets (the API, the portfolio Job and its
dispatcher, and the bars Job and its dispatcher) `reader` on the
`MainSequenceMarkets` branch, which lets them find the Markets API and write through it; the
migration Job and the Agent do not call it.

`MARKETS_API_URL` (with `MARKETS_API_TOKEN`) points a local process at a local Markets API.

## Operational Checks

```bash
mainsequence code-repository current --debug --json
mainsequence code-repository jobs list
mainsequence code-repository jobs run <JOB_UID> -- \
  --configuration-uid <CONFIGURATION_UID>
mainsequence code-repository jobs runs list <JOB_UID>
mainsequence code-repository jobs runs logs <JOB_RUN_UID> --max-wait-seconds 900
mainsequence code-repository images list
mainsequence code-repository resources list
```

Success requires a terminal successful JobRun, clean logs, and current output-table progress or
newly persisted rows. A queued run alone is not proof. Runtime attachment only binds already
migrated MetaTables; it never creates schema.

After a push, the migration Job logs one line per provider it applied. It no longer reports a
`markets_access` line: Markets reads no table of this application
([ADR 0031](../adrs/0031_markets_series_are_published_not_shared.md)), so there is no access of
Markets to check.
