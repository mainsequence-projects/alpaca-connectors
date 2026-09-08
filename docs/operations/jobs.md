# Jobs And Deployment

## Universe-Backed ETF Signal Jobs

Every stored signal configuration projects to its own Main Sequence Job. Different Universes can
therefore have different accounts, schedules, compute requests, and lifecycle state. The durable
record is `AlpacaETFSignalJobConfiguration`; JobRuns are execution history only.

Create and operate a signal Job through the CLI:

```bash
alpaca-connectors signal create \
  --name "Daily S&P 500 observation" \
  --universe-uid <UNIVERSE_UID> \
  --account-uid <ACCOUNT_UID> \
  --schedule-type crontab \
  --schedule-expression "0 9 * * 1-5" \
  --schedule-timezone America/New_York

alpaca-connectors signal list
alpaca-connectors signal run <CONFIGURATION_UID>
alpaca-connectors signal runs <CONFIGURATION_UID>
alpaca-connectors signal pause <CONFIGURATION_UID>
alpaca-connectors signal resume <CONFIGURATION_UID>
alpaca-connectors signal reconcile <CONFIGURATION_UID>
```

Creation stores desired state, materializes the deterministic ms-markets Signal metadata, creates
an initially unscheduled Job with automatic deployment, links its UID, then activates the schedule.
It does not publish weights. The launcher accepts no business arguments. It resolves the
configuration from `JOB_RUN_UID` and the owning Job UID, then runs one Universe-backed signal
update. Environment scope is derived from the current CodeRepositoryBranch and is never a caller
argument. See [ADR 0007](../adrs/0007_one_signal_configuration_per_job.md).

The API exposes the same lifecycle at `/v1/signal-jobs`, including list discovery, CRUD,
run/pause/resume/reconcile actions, and per-configuration JobRun history.

`GET /v1/signal-jobs/{configuration_uid}/observations?limit=100` is the read-only inspection
surface for published weights. It resolves the stable signal identity from the saved configuration
and returns the latest 100 distinct frames from `SignalWeightsStorage` as aligned timestamps,
Assets, and weights. The static site displays timestamps as rows and Assets as columns. Opening
signal details triggers this query; listing signals does not.

The browser form generates calendar schedules from daily, weekday, weekly, or monthly controls and
shows the resulting five-field expression. Advanced numeric expressions support wildcards, lists,
ranges, and steps; the API validates both syntax and the legal range of every cron field before it
stores desired state. Every crontab may select an IANA timezone such as `America/New_York` or
`Europe/Vienna`; abbreviations and fixed-offset `Etc/GMT` zones are rejected. The platform
evaluates the local cron clock through daylight-saving transitions. New backend Jobs that omit the
timezone default to UTC, while updates that omit it preserve the Job's current timezone. Interval
schedules never carry a timezone.

The Job schedule and the `TimeIndexTableUpdater` configuration are separate contracts. The schedule
decides when the JobRun starts. `AlpacaETFHoldingsSignal` receives `universe_uid` and the runtime
`account_uid`, and its output remains the canonical `SignalWeightsStorage` grain. ms-markets nests
that producer input under `SignalWeightsConfiguration.signal_configuration` and deliberately
excludes it from the canonical shared `SignalWeights` update hash; `signal_uid` separates each
Universe's observations in storage. The dedicated Job configuration row is the durable source for
reconstructing those runtime values.

With Main Sequence SDK 8.1, each `run()` call performs one update cycle directly. The removed
`force_update` and `debug_mode` parameters are not configuration fields and are not exposed in the
GUI, API, CLI, or Job launcher. This producer therefore publishes one observation on every
successful call, including unchanged weights. Dependency-tree execution remains a run control. The
inherited `offset_start` updater field is not exposed because this producer observes the provider's
current holdings and cannot backfill historically effective ETF weights.

## ETF Portfolio Jobs

Every durable Portfolio Configuration owns one dedicated Main Sequence Job. The Portfolio
Configuration stores only calculation inputs and its `job_uid` relationship. Schedule, compute,
spot, maximum runtime, automatic deployment, and image state remain exclusively on the Job.

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
  --rebalance-configuration-uid <REBALANCE_CONFIGURATION_UID> \
  --schedule-type crontab \
  --schedule-expression "0 20 * * 1-5" \
  --schedule-timezone America/New_York

alpaca-connectors portfolio run <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio runs <PORTFOLIO_CONFIGURATION_UID>
```

The Job is first created without a schedule, linked to the Portfolio Configuration, and then
patched with its schedule. Automatic deployment is always enabled. The launcher accepts no
business arguments and resolves its definition through
`JOB_RUN_UID -> Job.uid -> Portfolio Configuration.job_uid`.

For crontab schedules, timezone is stored only on that Job. Portfolio API responses read the live
Job and return both its IANA timezone and whether it was explicitly supplied; a backend-defaulted
UTC schedule is therefore distinguishable from an explicit UTC choice. Portfolio Configuration
rows do not duplicate either field.

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

## Current platform blocker

Live verification on 2026-09-03 proved that JobRun creation persists the requested
`command_args`, but the deployed runtime wrapper does not forward them to the Python script. Run
`d060f538-d63a-4328-bce9-979643876033` recorded the exact two arguments and then invoked the
launcher without them, causing its required-argument validation to fail. A second run with an
explicit separator produced the same result.

This conflicts with the current SDK/CLI Job argument contract. Do not weaken the launcher, move the
configuration into an environment variable, or retire the transitional Job to hide the platform
failure. The runtime executor must forward `JobRun.command_args` to the saved Python entrypoint;
after that correction, repeat the live smoke test with a real enabled configuration before the
cutover.

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
