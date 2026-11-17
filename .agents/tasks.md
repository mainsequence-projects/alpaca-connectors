# Tasks

## Open

### Roll out ADR 0027 (one portfolio path)

- Scope: in each Environment, list what the removed product holds and confirm the instance
  backfill ran; release R1 (the connector's own calendar helper, imports untangled, every delete
  retires, no create path for the removed objects, no signal schedule); drain each Environment with
  R1's CLI; release R2 (removal, revision `0027`, the `alpaca-portfolio` Jobs, 13 tools, 4 skills)
  and grant the two new Job workloads their roles before the next 02:00 UTC run; release R3
  (revision `0028`, the old launchers once no Job is bound to them).
- Required proof: every list of the inventory empty in every Environment, the guard test passing,
  and Hunch's integration tests passing against an Environment that is not Production after R1 and
  after R2.

### Live-verify one intended Alpaca account

- Scope: use explicitly selected Main Sequence Secret names to plan/register one paper or live
  account, capture holdings, create and resolve a stored bar configuration, execute it, and query
  the derived dataset's persisted observations.
- Required proof: stable Account UID, one detail row containing names but no values, canonical
  AccountHoldingsSet rows, stable configuration UUID, nonzero asset-indexed prices, and idempotent
  reruns.

### Publish and verify the FastAPI ResourceRelease

- Scope: commit and synchronize this refactor, deploy the validated API workflow, then exercise
  health, project state, resource reads, one safe preflight, CORS, and delegated identity through
  the release URL.
- Required proof: ready release, browser/API responses, and an explicit chosen route-edit policy.

## Completed

### Store and resolve Alpaca bar configurations

- Output: migration `0004`, durable configuration and explicit-membership MetaTables, CRUD through
  CLI and FastAPI, and update execution by configuration UID only.
- Source semantics: `assets`, `account_holdings`, and a third source that ADR 0027 later removed;
  the holdings source selects the newest persisted snapshot in the inclusive trailing 30-day window
  without capturing a new one.
- Validation: backend migration finalized both new MetaTables, the stored-config job workflow
  passed backend API 2.1.0 validation, and the repository passes 115 tests, Ruff, and strict
  MkDocs.

### Implement the capability-oriented API and CLI backend

- Output: shared services and matching CLI/FastAPI surfaces for Accounts, Holdings, Assets,
  historical-price datasets, and the extraction sources that ADR 0027 later removed.
- Validation: 115 tests, Ruff, dependency checks, strict docs build, exact
  Command Center schema validation, live CLI reads, and live FastAPI lifespan/read smoke tests.
- Scope boundary: no trading/order submission, arbitrary time-series mutation, new frontend, or
  Streamlit refactor.

### Apply project migrations through revision 0004

- Output: account Secret-name bindings, an extraction-source table (removed by ADR 0027) and
  operation tracking, plus durable Alpaca bar configuration and explicit-membership MetaTables.
- Validation: backend reports `0004 (head)`, eight active provider resources including the Alembic
  registry, zero reserved/failed, and successful runtime attachment of the complete model graph.

### Preserve earlier SDK and ms-markets migrations

- Main Sequence SDK 9, `mainsequence-metatable` 0.1, and ms-markets 2 were the targets then;
  MetaTables imports and migration commands use `metatables`.
- The existing reviewed daily price job remains the only scheduled job.
