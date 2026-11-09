# Jobs And Deployment

## Signal And Portfolio Jobs

Signal and portfolio configurations run on two Jobs declared in
`.mainsequence/workflows/alpaca-connectors-api.yaml`, one per kind, and two dispatcher Jobs in the
same file start their scheduled runs
([ADR 0013](../adrs/0013_workflow_declared_configuration_jobs.md)). No code creates a Job: since
`mainsequence` 9.0.2 only a workflow declaration can, and the push that carries a declaration creates
its Job.

| Key | Display name | Daily schedule | Arguments |
|---|---|---|---|
| `alpaca-etf-signal` | `Alpaca ETF Signal` | none | `--configuration-uid <uid>` per run |
| `alpaca-etf-portfolio` | `Alpaca ETF Portfolio` | none | `--configuration-uid <uid>` per run |
| `alpaca-etf-signal-dispatcher` | `Alpaca ETF Signal Dispatcher` | `30 0 * * *` UTC | `--scheduled` |
| `alpaca-etf-portfolio-dispatcher` | `Alpaca ETF Portfolio Dispatcher` | `0 2 * * *` UTC | `--scheduled` |

The dispatchers run after the US equity close and after the 00:00 UTC
[bars dispatcher](#daily-bars-dispatcher). All four Jobs
use 0.25 CPU, 0.5 GiB and 3600 seconds, and every push promotes their image. No `execution.steps`
entry references them, so a push may promote their image before the migration Job finishes; the
schema gate in each entry point turns a run in that window into a clean skip
([ADR 0012](../adrs/0012_deployment_workflow_migrations_and_schema_gated_jobs.md)).

How they run:

- **A manual run executes one configuration.** These all start a run of the kind's Job with the
  arguments `--configuration-uid <uid>`:
  - `POST /v1/signal-jobs/{uid}/actions/run` and `POST /v1/portfolio-configurations/{uid}/actions/run`;
  - `alpaca-connectors signal run <uid>` and `alpaca-connectors portfolio run <uid>`.

  A run is refused while the configuration's newest run is pending or running, or while the Job's
  image is not ready, which includes the image build after each push. A signal run needs an active
  Universe and Account. A portfolio run needs an allowed preflight: its linked configurations must
  exist and be enabled, and its signal must have published observations.
- **The scheduled run belongs to the dispatcher and executes nothing itself.** Each dispatcher
  starts one `--configuration-uid` run of its kind's Job for each configuration whose scheduled flag
  is true. It may, because it declares `runner` on that Job. The kind's Job cannot start its own
  runs: starting a run needs `runner` on the Job, and a Job cannot declare access to itself
  (`self_reference`).
  - It skips configurations with a pending or running run, and portfolios whose preflight is
    blocked.
  - It prints what it launched, skipped and failed, and fails if any start failed.
  - The kind's Job accepts only `--configuration-uid`; a run with `--scheduled` fails with a usage
    error.
- **`scheduled` is the only per-configuration operational setting**, and it defaults to true.
  - For a signal, `POST /v1/signal-jobs/{uid}/actions/pause` and `resume` set it. Signal rows store
    it in the `enabled` column.
  - `PATCH` sets it for either kind, and so do the CLI's `--scheduled` and `--no-scheduled`.
  - A configuration left out of the scheduled run can still be run manually.
- **Schedule and compute belong to the declarations.** To change them, edit the workflow file and
  push. A push re-applies the declared schedule, so a schedule edited on the platform lasts only
  until the next push. A push changes only the fields a declaration states, so the two
  configuration Jobs declare `task_schedule: null` and empty `scheduled_command_args`: that removes
  the schedule earlier pushes gave them. Requests that still send schedule or compute settings get
  422.
- **Run history comes from each Job's 500 newest runs.** Configuration responses report the kind's
  Job as `job_uid` and `job`, with its dispatcher's schedule. Both are null until the push creates
  the Job, and the schedule is null while the dispatcher is missing. Run lists and
  `/v1/operations/job-runs/{uid}` find a configuration's runs by their `--configuration-uid`
  argument. Dispatcher runs are not configuration runs, so that route does not return them.

A dispatcher starts runs of another Job from inside a Job runtime, with its own workload's
`runner` policy. This has not yet been checked on a hosted runtime: confirm it on the first
scheduled runs after the push (00:00, 00:30 and 02:00 UTC, the first being the bars dispatcher's).
Each dispatcher's workload User also needs its
MetaTables roles before then
([Table Access And MetaTables Roles](#table-access-and-metatables-roles)).

### Cleanup Of Per-Configuration Jobs

Before ADR 0013, the API created one Job per configuration. That worked with SDK 8, from 2026-09-04
until the repository moved to `mainsequence` 9.0.2 on 2026-10-01. Such a Job has no workflow
binding, so no push deletes it. It keeps its schedule:

- On its old image it still resolves its configuration through `job_uid` and repeats the work of
  the declared Job.
- If a push promotes it to the new image, it runs without a mode and fails with a usage error.

On each platform branch, after the push that creates the two declared Jobs:

1. List the candidates from a checkout of the branch:
   `mainsequence code-repository jobs list --filter "name__contains=Alpaca ETF"`.
   - The four declared Jobs are named exactly `Alpaca ETF Signal`, `Alpaca ETF Portfolio`,
     `Alpaca ETF Signal Dispatcher` and `Alpaca ETF Portfolio Dispatcher`.
   - A per-configuration Job is named `Alpaca ETF Signal — <configuration name> [<UID prefix>]` or
     `Alpaca ETF Portfolio — <configuration name> [<UID prefix>]`.
   - On 2026-10-05 the `main` branch on the development server had two:
     `0039edbc-f98a-418b-8237-e935c3a1bf3c` (signal) and
     `c099820c-8b63-420a-aeb6-9672ae0112cd` (portfolio).
2. Delete each per-configuration Job in the platform UI, or with
   `uv run python -c "from mainsequence.client import Job; Job.destroy_by_uid('<Job UID>')"`.
   The `mainsequence` 9.0.5 CLI has no Job delete command. Deleting a Job also deletes its JobRuns.
3. Check that the list from step 1 shows only the four declared Jobs.

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

## ETF Weight Signals

A signal configuration is one Universe plus the registered Alpaca account its runs use. The durable
record is `AlpacaETFSignalJobConfiguration`; JobRuns are execution history only.

```bash
alpaca-connectors signal create \
  --name "Daily S&P 500 observation" \
  --universe-uid <UNIVERSE_UID> \
  --account-uid <ACCOUNT_UID>

alpaca-connectors signal list
alpaca-connectors signal run <CONFIGURATION_UID>
alpaca-connectors signal runs <CONFIGURATION_UID>
alpaca-connectors signal pause <CONFIGURATION_UID>
alpaca-connectors signal resume <CONFIGURATION_UID>
alpaca-connectors signal reconcile <CONFIGURATION_UID>
```

Creation stores the configuration and materializes the deterministic ms-markets Signal metadata. It
does not publish weights and does not touch the platform. `reconcile` only ensures that metadata
row. Environment scope is derived from the current CodeRepositoryBranch and is never a caller
argument. See [ADR 0007](../adrs/0007_one_signal_configuration_per_job.md) for the signal identity
rules that remain.

The API exposes the same lifecycle at `/v1/signal-jobs`: list discovery, CRUD, run, pause, resume
and reconcile actions, and per-configuration JobRun history.

`GET /v1/signal-jobs/{configuration_uid}/observations?limit=100` is the read-only inspection
surface for published weights. It resolves the stable signal identity from the saved configuration
and returns the latest 100 distinct frames from `SignalWeightsStorage` as aligned timestamps,
Assets, and weights. The static site displays timestamps as rows and Assets as columns. Opening
signal details triggers this query; listing signals does not.

The Job schedule and the `TimeIndexTableUpdater` configuration are separate contracts. The schedule
decides when the JobRun starts. `AlpacaETFHoldingsSignal` receives `universe_uid` and the runtime
`account_uid`, and its output remains the canonical `SignalWeightsStorage` grain. ms-markets nests
that producer input under `SignalWeightsConfiguration.signal_configuration` and deliberately
excludes it from the canonical shared `SignalWeights` update hash; `signal_uid` separates each
Universe's observations in storage. The configuration row selected by `--configuration-uid` is the
durable source for reconstructing those runtime values.

Each `run()` call performs one update cycle directly. The removed `force_update` and `debug_mode`
parameters are not configuration fields and are not exposed in the GUI, API, CLI, or Job launcher.
This producer therefore publishes one observation on every successful call, including unchanged
weights. Dependency-tree execution remains a run control. The inherited `offset_start` updater field
is not exposed because this producer observes the provider's current holdings and cannot backfill
historically effective ETF weights.

## ETF Portfolios

A Portfolio Configuration stores only calculation inputs and its scheduled flag. Schedule, compute,
spot, maximum runtime, automatic deployment, and image state belong to the declared portfolio Job.

```bash
alpaca-connectors portfolio rebalance create \
  --name "NYSE close" \
  --strategy calendar_event_signal \
  --calendar-identifier NYSE \
  --session-label regular \
  --rebalance-event market_close \
  --rebalance-cadence every_session

alpaca-connectors portfolio create \
  --name "Daily IVV analytical portfolio" \
  --signal-configuration-uid <SIGNAL_CONFIGURATION_UID> \
  --bars-configuration-uid <BARS_CONFIGURATION_UID> \
  --rebalance-configuration-uid <REBALANCE_CONFIGURATION_UID>

alpaca-connectors portfolio run <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio runs <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio update <PORTFOLIO_CONFIGURATION_UID> --no-scheduled
```

Execution reads an existing Signal observation, updates the configuration-derived persistent
`InterpolatedPrices`, and calculates `PortfoliosUpdater` with dependency traversal disabled. It
does not rerun Universe extraction, the Signal producer, or the raw Bars updater. Revision `0010`
and the configuration-derived interpolation storage must be migrated before the first live run.
See [ADR 0008](../adrs/0008_portfolio_configuration_and_job_ownership.md).

Prepare that dynamic storage through the supported migration wrapper:

```bash
alpaca-connectors portfolio prepare-interpolated-prices
alpaca-connectors portfolio prepare-interpolated-prices --check-only
```

The first command generates a provider-scoped Alembic revision only when one is missing, upgrades
the shared project history, and verifies the registered output tables. The second is read-only and
fails if either the local revision or platform table is missing.

## Alpaca Bars Update Job

The canonical market-data Job is declared by:

- `src/jobs/run_alpaca_bars_update.py`;
- `.mainsequence/workflows/alpaca-bars-update.yaml`.

The Job is named `Alpaca Bars Update`. It is generic: each run supplies only the public UID of an
enabled stored bars configuration. The API starts it on demand, and the
[bars dispatcher](#daily-bars-dispatcher) in the same file starts it every day for each enabled
configuration.

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
([ADR 0020](../adrs/0020_daily_bars_dispatcher.md)), the bars counterpart of the signal and
portfolio dispatchers:

| Key | Display name | Daily schedule | Arguments |
|---|---|---|---|
| `alpaca-bars-update` | `Alpaca Bars Update` | none | `--configuration-uid <uid>` per run |
| `alpaca-bars-update-dispatcher` | `Alpaca Bars Update Dispatcher` | `0 0 * * *` UTC | `--scheduled` |

- **It starts the daily runs and updates nothing itself.** Its scheduled run
  (`src/jobs/dispatch_alpaca_bars_update.py`) starts one `--configuration-uid` run of
  `Alpaca Bars Update` for each enabled bar configuration, through the `runner` policy it declares
  on that Job. It must live in this file, because `access.resources` names only resources of the
  same file.
  - It skips a configuration whose run is pending or running among the Job's 500 newest runs.
  - It runs no preflight: each run resolves its own configuration and records its own failure.
  - It prints what it launched, skipped and failed, and fails if any start failed.
- **`enabled` is the scheduled flag.** Bar configurations have no `scheduled` field, so every
  enabled configuration is updated daily. To leave the daily run, disable the configuration
  (`alpaca-connectors market-data bar-configuration update <uid> --no-enabled`, or `PATCH` with
  `enabled: false`). That also refuses its manual runs and blocks the portfolios linked to it.
- **00:00 UTC is the earliest daily time that includes the session just closed.** The `1d` updater
  takes only bars that start before the current UTC midnight, and US sessions close at 20:00 UTC
  in summer and 21:00 UTC in winter. The run precedes the 00:30 UTC signal and 02:00 UTC portfolio
  dispatchers, so the bars runs have two hours before the portfolio runs read them. The Sunday and
  Monday 00:00 UTC runs find no new session.
- **Its compute matches the bars Job's** (0.25 CPU, 0.5 GiB, 3600 seconds, no spot), and every
  push promotes it. It declares no Secret: it never opens account keys, the runs it starts do.
- **`GET /v1/operations/job-runs/{uid}` does not return the dispatcher's runs,** only the
  `Alpaca Bars Update` runs it starts.

Its workload User needs `data_viewer` on `alpaca-connectors` before the first scheduled run
([Table Access And MetaTables Roles](#table-access-and-metatables-roles)).

### Run arguments

The bars Job and the signal and portfolio Jobs receive their per-run arguments through
`JobRun.command_args`. Live verification on 2026-09-03 showed the runtime wrapper dropping them: run
`d060f538-d63a-4328-bce9-979643876033` recorded the two arguments but invoked the launcher without
them, so its required-argument validation failed.

PodDeploymentOrchestrator `09e5ab4` (2026-09-13, "preserve script child argv") passes them to the
script as `sys.argv[1:]`, and tdag-django `01f475a86` appends `JobRun.command_args` to the runtime
command. No hosted run has confirmed the fix yet. A run that loses its arguments fails with a usage
error and does no work. Do not weaken a launcher or move a configuration into an environment
variable to work around a missing argument; repeat the smoke test with a real configuration
instead.

## Credential Migration Job

The API workflow also declares the one-off `alpaca-credential-migration` Job
(`src/jobs/migrate_account_credentials.py`, [ADR 0017](../adrs/0017_encrypted_account_credentials.md)).
No step references it and it has no schedule. After revision `0018` has been applied and the
`ALPACA_CONNECTORS_DATA_KEY` Secret exists, an operator runs it once per Environment: first as a
dry run, then with `--execute`, which copies every registration's keys from its Main Sequence
Secrets into the encrypted account columns and deletes the Secrets this application created.
The launcher starts with the schema gate, prints counts, Secret names and reasons (never a key
value), and exits non-zero when a registration is blocked:

```bash
mainsequence code-repository jobs run <JOB_UID>
```

```bash
mainsequence code-repository jobs run <JOB_UID> -- --execute
```

Like the API, the bars Job and the signal Job, it declares `value_reader` on the Secret in its
`access.secrets`; the schema migration Job, the portfolio Job and the dispatchers do not read keys
and have no such grant.

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
  ([ADR 0020](../adrs/0020_daily_bars_dispatcher.md)). It updates every enabled configuration at
  00:00 UTC through `Alpaca Bars Update`. Until it was declared, nothing updated bars on a
  schedule.

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
A person, or a Team of people, owns each namespace; a workload never owns one, never grants, and
never creates a table outside a namespace.

In each Environment where the project runs:

1. ms-markets must already be deployed and migrated there by its own deployment, which owns the
   `mainsequence.markets` schema, and its Markets API must serve the release this image runs
   (the Jobs' schema gate checks its `/health`). The project's revisions reference its tables by
   physical name.
2. Each workload holds these roles on `alpaca-connectors`. A workload User exists once its
   declaration is applied, and a recreated resource, such as a renamed key, has a new workload
   User that needs its roles again.

   | Workload | `alpaca-connectors` |
   |---|---|
   | `alpaca-connectors-migrate` | `editor`: it registers the project tables |
   | `alpaca` API | `editor`: it writes project rows and runs the signal (Universe run) and the orders and trades updaters in-process |
   | `alpaca-etf-signal` | `editor` |
   | `alpaca-etf-portfolio` | `editor` |
   | `alpaca-bars-update` | `data_viewer`, plus table `editor` on `alpaca_connectors__bars_1d_sip_all` and `alpaca_connectors__bars_1d_iex_raw` |
   | `alpaca-etf-signal-dispatcher`, `alpaca-etf-portfolio-dispatcher`, `alpaca-bars-update-dispatcher` | `data_viewer` |
   | `alpaca-credential-migration` (one-off) | `data_writer` |
   | `alpaca-connectors-agent` | none: its tools run in the API |

   The Markets workload holds `data_viewer` on `alpaca_connectors__portfoliosts__main`,
   `alpaca_connectors__portfolioweightsts__main` and `alpaca_connectors__signalweightsts__main`,
   which the Markets portfolio dataset catalog verifies and reads, and nothing else here: never a
   role on `alpaca_connectors__acct_alpaca`, whose columns hold the application-encrypted keys.

   Shared roles can stay on the Team, with its person owner as a member, so a recreated workload
   needs only membership. Grant the `editor` roles directly.
3. Every one of those resources except the Agent declares
   `access.branches: [{repository: MetaTables, policy: reader}]` in its workflow file. A migration
   that fails with `namespace_access_required`, `namespace_not_writable` or `table_not_editable`
   (403) names the `caller_user_uid` that lacks a role: the owner grants it. A manual run of the
   migration Job then applies the revisions, but only another push deploys the API that waited for
   them. Do not run the Job as an Organization admin to get around a refusal.

An Organization admin's ability to administer the platform does not by itself give a workload
table access. The Harness Agent receives no table role.

## Markets API Access

The connector reaches Markets through the Markets API (ms-markets ADR 0047): every entrypoint
selects ms-markets' remote row backend (`alpaca_connectors.runtime.use_markets_api`), attaches only
its own `alpaca-connectors` tables, and reads and writes Markets registries with `msm.api` row
calls, `AccountHoldingsSnapshots`, `AssetSnapshots` and `PortfolioDatasets`. In each Environment
the workflows grant every resource that calls Markets (the API, the signal and portfolio Jobs and
their dispatchers, the bars Job and its dispatcher, and the credential migration) `reader` on the
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
