# ADR: One Signal Configuration Per Job

## Status

Accepted

## Context

Each Universe-backed ETF signal needs its own schedule, compute request, Alpaca account, and
lifecycle. Different Universes must be independently pausable and schedulable. A Main Sequence
`JobRun` is an execution record, not durable desired state, and a scheduled Job cannot rely on a
human supplying `universe_uid` and `account_uid` for every occurrence.

The runtime also must not confuse operational configuration with signal identity. `universe_uid`
defines the signal while `account_uid` only selects the registered Secret references used during
the update.

## Decision

The project stores one `AlpacaETFSignalJobConfiguration` MetaTable row per signal Job. A row owns:

- one active `AssetUniverse`;
- one active registered Alpaca account used only at runtime;
- an interval or five-field crontab schedule;
- CPU, memory, maximum runtime, and spot preferences;
- one linked Main Sequence Job UID and reconciliation state.

The table has unique indexes on `universe_uid` and `job_uid`. MetaTables and Jobs are scoped through
the current CodeRepositoryBranch. The API, GUI, and CLI never accept an Environment selector;
Main Sequence resolves the exact Organization Environment from repository/runtime context.

Creation is an ordered reconciliation:

```text
store desired row as provisioning
  -> upsert the deterministic ms-markets SignalMetadata row
  -> create an unscheduled Job with automatic deployment
  -> store the returned Job UID
  -> patch the Job with the requested schedule
  -> mark the row ready or paused
```

This ordering prevents a scheduled occurrence from starting before its Job can resolve a durable
configuration. Signal creation does not publish a weights observation; the first observation is
written only by a successful JobRun.

Manual and scheduled JobRuns carry no business arguments. The launcher resolves:

```text
JOB_RUN_UID -> JobRun.job_uid -> AlpacaETFSignalJobConfiguration.job_uid
```

It then invokes one `AssetUniverse` execution. That execution extracts once, bulk-registers
missing assets, bulk-replaces category membership, and writes one complete observed weights frame
through `AlpacaETFHoldingsSignal`.

## Consequences

- Scheduling two Universes creates two Jobs, not two configurations on one generic Job.
- A Job may have many JobRuns, but a JobRun never owns or overrides signal configuration.
- Updating the account changes runtime access without changing the final signal UID or storage
  dimensions.
- Signal metadata exists immediately after successful reconciliation and the API exposes its
  deterministic `signal_uid`.
- Callers cannot select an Environment; the branch-owned Job and platform-managed tables resolve it
  automatically.
- Pause/resume updates both desired state and the Job schedule. Reconcile repairs drift or a
  missing Job.
- Deleting a configuration deletes its dedicated Job and row but retains published signal
  observations.
- Account and Universe deletion are blocked while referenced by a signal Job configuration.
