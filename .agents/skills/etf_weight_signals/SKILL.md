---
name: alpaca-etf-weight-signals
description: Create, inspect, schedule, and run Universe-backed Alpaca ETF weight signal configurations on the shared, workflow-declared signal Job.
---

# Alpaca ETF Weight Signals

Use this skill for durable `AlpacaETFSignalJobConfiguration` objects. In a CodeRepository Executor
session, use `alpaca_query_etf_signals` and `alpaca_manage_etf_signal`. In a shell, use the
installed `alpaca-connectors` command.

## Signal Contract

- `universe_uid` defines business identity and the stable final `signal_uid`.
- `account_uid` is serialized updater/runtime configuration used to resolve Alpaca Secret names and
  register constituents. It is excluded from `_signal_uid_payload()` and never becomes a
  `SignalWeightsStorage` column or index dimension.
- Every configuration runs on the `Alpaca ETF Signal` Job declared in
  `.mainsequence/workflows/alpaca-connectors-api.yaml`, which owns the daily schedule (00:30 UTC)
  and compute. A configuration only sets `scheduled`: whether that Job's scheduled run includes it.
  Never send schedule or compute fields; the API rejects them. A run selects its configuration with
  `--configuration-uid`; JobRuns are execution history and never own the signal configuration.
- Every successful update extracts the Universe source, bulk-registers missing constituents,
  replaces linked category membership, and publishes one complete weight observation—even when
  weights did not change.
- The timestamp is when extraction observed the weights; it does not guarantee their exact economic
  effective time.

## Workflow

1. Query Universes and Accounts, then create one signal configuration using
   `alpaca_manage_etf_signal` operation `create`.
2. Verify the returned stable signal UID, `scheduled`, lifecycle state, and the shared Job's
   schedule. `job_uid` and `job` are null and the state is `provisioning` until a push has created
   the Job.
3. Use operation `run` for an on-demand JobRun. Do not reimplement the extraction inline.
4. Poll `alpaca_get_job_run_status`, then read operation `observations` for up to the latest 100
   complete frames or operation `runs` for execution history.
5. Update or delete only the selected configuration; operation `update` with `scheduled: false`
   leaves it out of the scheduled run. Deletion retains published signal data and JobRuns.

Shell equivalents:

```shell
alpaca-connectors signal list
alpaca-connectors signal get <CONFIGURATION_UID>
alpaca-connectors signal run <CONFIGURATION_UID>
alpaca-connectors signal runs <CONFIGURATION_UID>
```

Do not expose pause, resume, or reconcile actions as agent operations; `scheduled` covers pausing.
Tau deletion requires exact `DELETE <configuration_uid>` confirmation.
