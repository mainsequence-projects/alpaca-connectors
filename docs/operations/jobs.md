# Jobs And Scheduling

## Repository-managed schedule

The recurring daily bar job is declared in:

- `.mainsequence/workflows/daily-stock-bars-holdings-ivv.yaml`
- `src/jobs/run_daily_stock_bars_holdings_ivv.py`

The workflow uses the current CodeRepository workflow contract (`api_version: 2.1.0`) and runs at
midnight UTC with `cpu_request: "0.25"`, `memory_request: "0.5"`, and `spot: false`.

The workflow was validated against the backend branch contract during the SDK 8 migration. The
backend applies repository-managed workflows only after the reviewed file is committed and pushed
through the normal CodeRepository sync. Git success is not deployment success; verify the resulting
job and repository event separately.

```bash
mainsequence code-repository sync -m "Migrate Alpaca connector to SDK 8"
mainsequence code-repository jobs list --path .
```

Do not restore `scheduled_jobs.yaml` or `schedule_batch_jobs`; both are removed compatibility
surfaces in the current SDK.

## Job behavior

The daily launcher updates bars for:

- category `HOLDINGS__IVV`
- cadence `1d`
- feed `sip`
- adjustment `all`

`HOLDINGS__IVV` must already exist as an ms-markets `AssetCategory`. The launcher consumes the
category; it does not create or refresh it.

The repository also contains `src/jobs/run_etf_maintenance_routines.py`, driven by
`data/etf_maintenance_routines.yaml`, for ordered manual ETF maintenance workflows. It is not
declared as a backend Job.

Preview those operations before executing them:

```bash
.venv/bin/python src/jobs/run_etf_maintenance_routines.py --dry-run
.venv/bin/python src/jobs/run_etf_maintenance_routines.py
```

## Backend prerequisites

Before any live job writes bars or categories:

1. Refresh the CodeRepository token and confirm the checkout mapping.
2. Verify the project-owned migration is at Alembic head.
3. Ensure the exact execution image contains Python 3.13, `mainsequence>=8.0.7`, and
   `ms-markets>=1.0.2` as resolved by `uv.lock`/`requirements.txt`.
4. Ensure the Main Sequence `ALPACA_API_KEY` secret is available to the job.

```bash
mainsequence code-repository refresh-token --path .
mainsequence code-repository current --debug --json
mainsequence migrations current --provider src.migrations:migration
mainsequence code-repository images list --path .
```

The launchers attach the already-migrated markets runtime with
`src.runtime.start_markets_engine()`; runtime startup never creates or repairs schema.

## Operational verification

After the workflow has been committed and processed:

```bash
mainsequence code-repository jobs list --path .
mainsequence code-repository jobs run <JOB_UID>
mainsequence code-repository jobs runs list <JOB_UID>
mainsequence code-repository jobs runs logs <JOB_RUN_UID> --max-wait-seconds 900
```

At migration time the backend job list was empty, so the repository workflow is the authoritative
declaration but still requires the normal commit/sync event before a scheduled Job exists.
