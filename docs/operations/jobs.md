# Jobs And Scheduling

## Current Job Target

The project includes:

- `scheduled_jobs.yaml`
- `src/jobs/run_daily_stock_bars_holdings_ivv.py`
- `src/jobs/run_etf_maintenance_routines.py`
- `data/etf_maintenance_routines.yaml`

Use the routine runner for looped ETF workflows (register, category sync, ETF prices, category prices):

Example:

```bash
.venv/bin/python src/jobs/run_etf_maintenance_routines.py --dry-run
```

Run `--dry-run` first to verify generated command order, then execute:

```bash
.venv/bin/python src/jobs/run_etf_maintenance_routines.py
```

That launcher runs daily bars for:

- `HOLDINGS__IVV`
- `1d`
- `sip`
- `all`

The fixed `src/jobs/run_daily_stock_bars_holdings_ivv.py` launcher assumes that
`HOLDINGS__IVV` already exists as an ms-markets `AssetCategory`. It consumes the
category; it does not create or refresh it.

## Storage-first prerequisites

The migration to ms-markets storage-first adds two requirements before any **live** (non
`--dry-run`) job writes bars or categories:

1. The project market tables must be migrated/registered once:
   `mainsequence migrations upgrade --provider markets_migrations:migration head`.
2. The job process attaches the markets runtime via `src.runtime.start_markets_engine()` — the
   job `main()` entrypoints already do this (skipped for `--dry-run`, which needs no backend).

## Schedule

The current crontab expression is:

```text
0 0 * * *
```

This means midnight UTC.

## File Shape

```yaml
jobs:
  - name: "Alpaca Daily Stock Bars HOLDINGS__IVV"
    execution_path: "src/jobs/run_daily_stock_bars_holdings_ivv.py"
    task_schedule:
      type: "crontab"
      expression: "0 0 * * *"
    related_image_id: 0
    cpu_request: "0.25"
    memory_request: "0.5"
```

## Important Operational Note

At the time this documentation was written, the project had no project images available from `mainsequence project images list`, so `related_image_id: 0` is only a placeholder.

Before scheduling the batch for real, a valid project image must exist.

## Submit The Batch

```bash
/bin/zsh -lc "set -a; source .env; export MAINSEQUENCE_AUTH_MODE=jwt; set +a; .venv/bin/mainsequence project schedule_batch_jobs scheduled_jobs.yaml"
```
