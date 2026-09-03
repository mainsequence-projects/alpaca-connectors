# Tasks

## Open

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
- Source semantics: `assets`, `universe`, and `account_holdings`; the holdings source selects the
  newest persisted snapshot in the inclusive trailing 30-day window without capturing a new one.
- Validation: backend migration finalized both new MetaTables, the stored-config job workflow
  passed backend API 2.1.0 validation, and the repository passes 115 tests, Ruff, and strict
  MkDocs.

### Implement the capability-oriented API and CLI backend

- Output: shared services and matching CLI/FastAPI surfaces for Accounts, Holdings,
  UniverseSource CRUD, materialized universes, Assets, and historical-price datasets.
- Validation: 115 tests, Ruff, dependency checks, strict docs build, exact
  Command Center schema validation, live CLI reads, and live FastAPI lifespan/read smoke tests.
- Scope boundary: no trading/order submission, arbitrary time-series mutation, new frontend, or
  Streamlit refactor.

### Apply project migrations through revision 0004

- Output: account Secret-name bindings, UniverseSource and operation tracking, plus durable Alpaca
  bar configuration and explicit-membership MetaTables.
- Validation: backend reports `0004 (head)`, eight active provider resources including the Alembic
  registry, zero reserved/failed, and successful runtime attachment of the complete model graph.

### Seed the initial user-maintained universe source

- Output: explicit, idempotent IVV starter row in `UniverseSourceTable`.
- Validation: live list returns source UID `28f98530-9380-4a3b-b70e-3da8b972c205` with the official
  US iShares URL; a repeat seed performs no write, and live preview returns 504 components.

### Preserve earlier SDK, ms-markets, and etfhextractor migrations

- SDK 8.0.7 and ms-markets 1.0.2 remain current.
- `etfhextractor 0.4.1` remains an external, unpinned-in-pyproject Git dependency.
- The existing reviewed daily price job remains the only scheduled job.
