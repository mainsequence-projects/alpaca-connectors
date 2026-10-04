# Jobs And Deployment

## Signal And Portfolio Jobs

Signal and portfolio configurations run on two Jobs declared in
`.mainsequence/workflows/alpaca-connectors-api.yaml`, one per kind
([ADR 0013](../adrs/0013_workflow_declared_configuration_jobs.md)). No code creates a Job: since
`mainsequence` 9.0.2 only a workflow declaration can, and the push that carries a declaration creates
its Job.

| Key | Display name | Daily schedule | Scheduled argument |
|---|---|---|---|
| `alpaca-etf-signal` | `Alpaca ETF Signal` | `30 0 * * *` UTC | `--scheduled` |
| `alpaca-etf-portfolio` | `Alpaca ETF Portfolio` | `0 2 * * *` UTC | `--scheduled` |

Both run after the 00:00 UTC daily bars Job and after the US equity close. Their compute is
0.25 CPU, 0.5 GiB and 3600 seconds, and every push promotes their image. No `execution.steps` entry
references them, so a push may promote their image before the migration Job finishes; the schema
gate in each entry point turns a run in that window into a clean skip
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
- **The scheduled run executes nothing itself.** It starts one `--configuration-uid` run for each
  configuration whose scheduled flag is true.
  - It skips configurations with a pending or running run, and portfolios whose preflight is
    blocked.
  - It prints what it launched, skipped and failed, and fails if any start failed.
- **`scheduled` is the only per-configuration operational setting**, and it defaults to true.
  - For a signal, `POST /v1/signal-jobs/{uid}/actions/pause` and `resume` set it. Signal rows store
    it in the `enabled` column.
  - `PATCH` sets it for either kind, and so do the CLI's `--scheduled` and `--no-scheduled`.
  - A configuration left out of the scheduled run can still be run manually.
- **Schedule and compute belong to the declarations.** To change them, edit the workflow file and
  push. A push re-applies the declared schedule, so a schedule edited on the platform lasts only
  until the next push. Requests that still send schedule or compute settings get 422.
- **Run history comes from each Job's 500 newest runs.** Configuration responses report the Job as
  `job_uid` and `job`, both null until the push creates it. Run lists and
  `/v1/operations/job-runs/{uid}` find a configuration's runs by their `--configuration-uid`
  argument.

The scheduled run starts other runs of its own Job from inside a Job. This has not yet been checked
on a hosted runtime: confirm it on the first scheduled run after the push.

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
   - The two declared Jobs are named exactly `Alpaca ETF Signal` and `Alpaca ETF Portfolio`.
   - A per-configuration Job is named `Alpaca ETF Signal — <configuration name> [<UID prefix>]` or
     `Alpaca ETF Portfolio — <configuration name> [<UID prefix>]`.
   - On 2026-10-05 the `main` branch on the development server had two:
     `0039edbc-f98a-418b-8237-e935c3a1bf3c` (signal) and
     `c099820c-8b63-420a-aeb6-9672ae0112cd` (portfolio).
2. Delete each per-configuration Job in the platform UI, or with
   `uv run python -c "from mainsequence.client import Job; Job.destroy_by_uid('<Job UID>')"`.
   The `mainsequence` 9.0.5 CLI has no Job delete command. Deleting a Job also deletes its JobRuns.
3. Check that the list from step 1 shows only the two declared Jobs.

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
`InterpolatedPrices`, and calculates `PortfoliosDataNode` with dependency traversal disabled. It
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

The Job is named `Alpaca Bars Update`. It is generic and on-demand: each invocation supplies only
the public UID of an enabled stored bars configuration.

```bash
mainsequence code-repository jobs run <JOB_UID> -- \
  --configuration-uid <CONFIGURATION_UID>
```

The launcher resolves the stored configuration at run time and always executes the update. It does
not accept a dataset UID, account override, asset-source override, bar-profile override, Secret
name, credential value, hash namespace, or removed `force_update` option. Main Sequence records the
two command arguments on the resulting JobRun.

The workflow uses backend API `2.2.0`, has no schedule or configuration environment variable, and
keeps automatic redeployment disabled. A recurring schedule needs a separate durable
schedule-to-configuration design because the current schedule declaration does not bind per-run
arguments.

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

## Transitional Scheduled Job

The previous scheduled declaration and environment-variable launcher remain in the checkout until
the generic Job has a ready image and one successful live update:

- `src/jobs/run_daily_stock_bars_holdings_ivv.py`;
- `.mainsequence/workflows/daily-stock-bars-holdings-ivv.yaml`.

After that proof, explicitly disable and delete the old remote Job, then remove those two local
files. Deleting the workflow file alone does not delete the platform Job. Do not treat both Jobs as
active production paths.

## FastAPI Release

`.mainsequence/workflows/alpaca-connectors-api.yaml` declares the registered FastAPI resource with
automatic deployment, revision retention, and the platform-provided static-site origin policy.
A valid workflow or successful Git push is not proof of deployment; verify the ResourceRelease and
its DeploymentRun separately.

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
