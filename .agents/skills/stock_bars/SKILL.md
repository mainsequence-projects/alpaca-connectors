---
name: alpaca-stock-bars
description: Create, inspect, and run stored Alpaca bar configurations for explicit Assets, Universe members, or recent Account holdings, and query their migrated price datasets.
---

# Alpaca Stock Bars

Use this skill for durable Alpaca bar configurations, bounded bar queries, and configuration-backed
updates. In a CodeRepository Executor session, use `alpaca_query_bar_configurations` and
`alpaca_manage_bar_configuration`. In a shell, use the installed `alpaca-connectors` command.

## Configuration Contract

Each stored configuration selects:

- one registered Account whose Secret names resolve Alpaca access;
- one migrated frequency, feed, and adjustment profile; and
- exactly one asset source: explicit registered Assets, one active Universe, or the latest Account
  holdings inside the inclusive trailing 30-day window.

The configuration resolver derives the Asset set and output dataset. Never accept a runtime dataset
UID, Account override, bar-profile override, or asset-scope override. Resolving recent Account
holdings is read-only and never captures a new snapshot.

## Workflow

1. List migrated datasets and stored configurations with `alpaca_query_bar_configurations`.
2. Create or update one configuration with `alpaca_manage_bar_configuration`.
3. Resolve it with query operation `resolve`; inspect the derived Asset identities and dataset.
4. When authorized, submit operation `run`. This creates a JobRun rather than running a large
   update inline in the agent session.
5. Poll `alpaca_get_job_run_status`, then query bounded observations using dataset operation
   `observations`.

Shell equivalents:

```shell
alpaca-connectors market-data bar-configuration list
alpaca-connectors market-data update --configuration-uid <CONFIGURATION_UID>
alpaca-connectors market-data update --configuration-uid <CONFIGURATION_UID> --execute
alpaca-connectors market-data prices --dataset-uid <DATASET_UID> --limit 100
```

Use set-based Asset/detail queries and batched Alpaca bar requests. Stop on missing or ambiguous
Alpaca identities instead of dropping Assets or issuing per-Asset fallback requests. Tau deletion
requires exact `DELETE <configuration_uid>` confirmation.
