# Tasks

## Open

### Apply And Verify The Repository-managed Daily Job

- Owning skill: `.agents/skills/mainsequence/platform_operations/orchestration_and_releases/SKILL.md`
- Scope: after reviewing and committing the migration changes, synchronize the CodeRepository so
  `.mainsequence/workflows/daily-stock-bars-holdings-ivv.yaml` is applied.
- Expected output: one scheduled backend Job for
  `src/jobs/run_daily_stock_bars_holdings_ivv.py` at `0 0 * * *` with an exact Python 3.13 image.
- Validation evidence: successful repository event, `code-repository jobs list`, one test run, and
  successful run logs. The backend job list was empty on 2026-09-02.

### Backfill And Verify SDK 8 Alpaca Bar Storage

- Owning skill: `.agents/skills/mainsequence/data_publishing/data_nodes/SKILL.md`
- Scope: plan, then execute, the first production update of the new
  `alpaca_connectors__bars_1d_sip_all` table for a controlled registered universe.
- Expected output: rows in the migrated table using `(time_index, asset_identifier)` grain and no
  dependency on legacy DataNode storage.
- Validation evidence: plan hashes, successful update logs, table update status, and row/date
  coverage.

### Live-verify Alpaca Account Registration

- Owning skill: `.agents/skills/ms_markets/accounts/account_workflow/SKILL.md`
- Scope: with explicit Alpaca account authorization, execute the paper-account registration path
  and validate current details plus canonical account holdings.
- Expected output: one ms-markets Account, one `AlpacaAccountDetails` row, and one holdings set with
  equity and cash holdings.
- Validation evidence: redacted row identities/counts, idempotent rerun, and confirmation that raw
  API credentials were not persisted.

## Completed

### Migrate The Project To SDK 8 And ms-markets 1

- Owning skills: project builder, DataNodes, API surfaces, ms-markets bootstrap, and orchestration.
- Output: Python 3.13; compatible dependency lower bounds; updated lock/export; output-table
  DataNode contract; FastAPI lifespan/runtime and registered-table reads; project calendar adapter;
  current CodeRepository workflow YAML; updated scaffolds and documentation.
- Validation evidence: SDK `8.0.7`, ms-markets `1.0.2`, backend workflow validation, focused tests,
  lint, and full-suite results recorded in `.agents/status.md`.

### Implement And Backend-verify The Full etfhextractor Migration

- Owning skills: ETF extraction/category/signal skills, Main Sequence updater/migration skills,
  and ms-markets portfolio/bootstrap skills.
- Output: Python 3.13 dependency floor; SDK 8 output-table contract; `TimeIndexTableRef`; reusable
  portfolio builder preserving custom identity; provider-scoped demo-bars migration; refreshed
  skills/docs/agent state; Alpaca delegation to the external builder.
- Validation evidence: external 86-test suite, Alpaca 83-test integration suite, backend revision
  `0001 (head)`, active registry/demo table, runtime attachment, and live IVV extraction.

### Publish And Lock etfhextractor 0.4.1

- Output: canonical upstream CodeRepository sync committed `036c8ba`, pushed remote `main`, and
  created tag `v0.4.1`; Alpaca's unpinned pyproject git source now resolves that exact commit in
  `uv.lock` and `requirements.txt`.
- Validation evidence: clean upstream checkout, tag at HEAD, installed package version `0.4.1`,
  and both full suites passing without a local dependency override.

### Apply Project-owned MetaTable Revision 0001

- Owning skills: Main Sequence MetaTable migrations and ms-markets MetaTable extensions.
- Output: Alembic revision `0001` and active project tables for SIP/all bars, IEX/raw bars, and
  Alpaca account details.
- Validation evidence: `mainsequence migrations current` returned `0001 (head)`; finalization
  returned four active resources and zero reserved/failed; runtime attachment resolved all project
  models and foreign-key dependencies.
