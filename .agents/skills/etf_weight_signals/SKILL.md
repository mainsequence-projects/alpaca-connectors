---
name: alpaca-etf-weight-signals
description: Create, inspect, schedule, and run Universe-backed Alpaca ETF weight signals with one durable Main Sequence Job per configuration.
---

# Alpaca ETF Weight Signals

Use this skill for durable `AlpacaETFSignalJobConfiguration` objects and their dedicated Jobs. In a
CodeRepository Executor session, use `alpaca_query_etf_signals` and `alpaca_manage_etf_signal`. In a
shell, use the installed `alpaca-connectors` command.

## Signal Contract

- `universe_uid` defines business identity and the stable final `signal_uid`.
- `account_uid` is serialized updater/runtime configuration used to resolve Alpaca Secret names and
  register constituents. It is excluded from `_signal_uid_payload()` and never becomes a
  `SignalWeightsStorage` column or index dimension.
- Each configuration owns one dedicated Main Sequence Job, including its schedule and compute
  settings. JobRuns are execution history and never own the signal configuration.
- Every successful update extracts the Universe source, bulk-registers missing constituents,
  replaces linked category membership, and publishes one complete weight observation—even when
  weights did not change.
- The timestamp is when extraction observed the weights; it does not guarantee their exact economic
  effective time.

## Workflow

1. Query Universes and Accounts, then create one signal configuration with schedule and compute
   details using `alpaca_manage_etf_signal` operation `create`.
2. Verify the returned stable signal UID, dedicated Job UID, lifecycle state, and schedule.
3. Use operation `run` for an on-demand JobRun. Do not reimplement the extraction inline.
4. Poll `alpaca_get_job_run_status`, then read operation `observations` for up to the latest 100
   complete frames or operation `runs` for execution history.
5. Update or delete only the selected configuration. Deletion retains published signal data.

Shell equivalents:

```shell
alpaca-connectors signal list
alpaca-connectors signal get <CONFIGURATION_UID>
alpaca-connectors signal run <CONFIGURATION_UID>
alpaca-connectors signal runs <CONFIGURATION_UID>
```

Do not expose pause, resume, or reconcile actions as agent operations. Tau deletion requires exact
`DELETE <configuration_uid>` confirmation.
