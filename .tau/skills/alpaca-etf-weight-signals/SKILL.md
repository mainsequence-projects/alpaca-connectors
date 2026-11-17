---
name: alpaca-etf-weight-signals
description: Inspect, run and remove the remaining Universe-backed ETF weight signal configurations, and read their observations and runs through Alpaca MCP tools.
---

# ETF Weight Signals

Discover schemas with `alpaca__list_tools`; use `alpaca__call_tool` with `tool` and `arguments`.
Direct MCP clients use inner names and arguments with `tools/call`. The API verifies identity.
Call `alpaca_project_state` with empty arguments for readiness when needed. No shell, inline
updater or platform Job-provisioning fallback exists.

## Identity And Observations

`universe_uid` defines stable business identity and final `signal_uid`. `account_uid` is
serialized runtime configuration to open the account's stored keys and register constituents. It is
excluded from signal identity and never becomes a storage column/index dimension. Weights retain
their standard `(time_index, signal_uid, asset_identifier)` grain.

Each successful update extracts the Universe, bulk-registers missing constituents, bulk-replaces
linked category members and publishes one complete observed frame, including unchanged weights.
Its timestamp is extraction observation time, not guaranteed economic effective time. Do not
backdate, skip unchanged frames or claim point-in-time-perfect economic ETF weights.

## Configure

No ETF signal is created any more, and no scheduled run takes new observations: the existing
configurations are read, run on demand and deleted until none is left. If asked to create one,
say so plainly.

`alpaca_query_etf_signals` supports `list`, `get`, `observations`, and `runs`;
`alpaca_manage_etf_signal` supports `update`, `run` and `delete`. Examples are arguments to
`alpaca__call_tool`; replace UID placeholders with returned UIDs.

Verify identity, lifecycle and linked Job. The shared workflow Job owns compute; never send
cron/compute fields or expose an Organization Environment selector. A null Job and provisioning
state means unavailable: report it, never create a replacement.

## Run And Inspect

Submit an authorized `run` with the stored configuration UID. Inspect
`alpaca_get_job_run_status` with the returned `job_run_uid` until terminal. Submission alone
is not publication. Report failed runs without claiming inserted rows.

```json
{"tool":"alpaca_manage_etf_signal","arguments":{"operation":"run","configuration_uid":"CONFIGURATION_UID"}}
```

Read up to the latest 100 complete frames with `observations`, or recent history with `runs`.
Frames contain all component weights; do not query once per Asset.

```json
{"tool":"alpaca_query_etf_signals","arguments":{"operation":"observations","configuration_uid":"CONFIGURATION_UID","limit":100}}
```

List limits are 1–100 with offset in multiples of the fixed limit; observations and run-history
limits are 1–100. Inspect `get` before `update`. Setting scheduled false omits future scheduled
runs, not cancellation of a running JobRun. No pause/resume/reconcile operation exists.

Deletion requires the person's exact `DELETE <configuration_uid>` confirmation; respect
portfolio reference blockers. Published weights and JobRuns are retained. JobRuns record execution,
never own configuration.
