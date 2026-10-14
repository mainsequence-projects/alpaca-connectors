---
name: alpaca-stock-bars
description: Configure and run Alpaca bars for Assets, Universe members or recent Account holdings, then query resolved migrated datasets through Alpaca MCP tools.
---

# Alpaca Stock Bars

Discover schemas with `alpaca__list_tools`; use `alpaca__call_tool` with `tool` and `arguments`.
Direct MCP clients use the inner names and arguments with `tools/call`. The API verifies identity.
Call `alpaca_project_state` with empty arguments for readiness when needed.
No shell, arbitrary SQL or direct updater fallback exists.

## Configure

`alpaca_query_bar_configurations` supports `list`, `get`, `resolve`, `datasets`, `dataset`,
and `observations`. Discover the Account, Asset scope and migrated frequency/feed/adjustment
profile before `alpaca_manage_bar_configuration` operations `create`/`update`.
Never invent profiles or dynamically create schemas.

A stored configuration selects one Account and exactly one source:

- `assets`: non-empty registered `asset_uids`, no Universe UID.
- `universe`: an active registered `universe_uid`, no explicit Asset UIDs.
- `account_holdings`: neither explicit Assets nor Universe. The configured Account resolves
  its newest stored holdings inside the inclusive trailing 30-day window; resolution is read-only
  and never captures holdings.

Collections use limit 1–100 and offset in multiples of the fixed limit. Examples are arguments to
`alpaca__call_tool`; replace placeholders with discovered identifiers/profile values:

```json
{"tool":"alpaca_query_bar_configurations","arguments":{"operation":"datasets","limit":25,"offset":0}}
```

```json
{"tool":"alpaca_manage_bar_configuration","arguments":{"operation":"create","request":{"name":"Universe daily bars","account_uid":"ACCOUNT_UID","asset_source":"universe","universe_uid":"UNIVERSE_UID","frequency_id":"MIGRATED_FREQUENCY_ID","feed":"MIGRATED_FEED","adjustment":"MIGRATED_ADJUSTMENT"}}}
```

## Resolve, Submit And Verify

1. Query `resolve` using the stored configuration UID; inspect derived Assets and output dataset.
   The schema also supports an optional hash namespace.
2. Fix missing/ambiguous provider identities before running. A unique stored Alpaca Asset/detail
   identity may resolve a current-catalog miss; FIGI is not required.
3. Submit authorized `run`. It returns a JobRun UID, not proof that prices updated.
4. Inspect `alpaca_get_job_run_status` with that UID at reasonable intervals until terminal.
   Report failures; do not claim success or repeatedly resubmit.
5. After success, query bounded observations with the dataset returned by resolution.

```json
{"tool":"alpaca_query_bar_configurations","arguments":{"operation":"resolve","configuration_uid":"CONFIGURATION_UID"}}
```

```json
{"tool":"alpaca_manage_bar_configuration","arguments":{"operation":"run","configuration_uid":"CONFIGURATION_UID"}}
```

```json
{"tool":"alpaca_get_job_run_status","arguments":{"job_run_uid":"JOB_RUN_UID"}}
```

Observation queries accept the resolved dataset UID, optional Asset filters, ISO-8601
`start`/`end`, limit 1–1000 and offset in multiples of the limit:

```json
{"tool":"alpaca_query_bar_configurations","arguments":{"operation":"observations","dataset_uid":"RESOLVED_DATASET_UID","limit":100,"offset":0,"ordering":"-time_index"}}
```

Run never accepts a dataset UID, Account/profile/scope override. The service uses set-based
identity queries and symbol-batched provider requests; do not drop constituents or make per-Asset
fallback calls. Deletion requires the person's exact `DELETE <configuration_uid>` confirmation;
respect dependency blockers and retain historical data.
