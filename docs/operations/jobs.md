# Jobs And Deployment

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
name, credential value, hash namespace, or `force_update` option. Main Sequence records the two
command arguments on the resulting JobRun.

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
