# Project Status

## Verified

- Repository checkout:
  `/Users/jose/mainsequence-dev/main-sequence-workbench/projects/alpacaconnectors-945bfddd-5f1f-4541-a87a-faea3af6271f`.
- CodeRepository UID: `c4980dd0-db33-420c-9a3f-050815a03512`.
- Active branch: `main`; CodeRepositoryBranch UID:
  `945bfddd-5f1f-4541-a87a-faea3af6271f`.
- `mainsequence code-repository current --debug` resolves the checkout and reports matching
  local/latest SDK requirement `8.0.7`.
- Runtime interpreter: Python `3.13.11`.
- Installed migrations/runtime packages: `mainsequence 8.0.7`, `ms-markets 1.0.2`.
- `pyproject.toml` uses compatible lower bounds; it does not exactly pin either package.
- `uv.lock` and exported `requirements.txt` are synchronized.
- `AGENTS.md` was refreshed successfully from the installed SDK 8 scaffold.
- The packaged ms-markets skill bundle was refreshed to `1.0.2`.
- Alembic provider `src.migrations:migration` is at revision `0001 (head)`.
- The project Alembic registry and all three provider tables finalized active with zero reserved or
  failed resources:
  - `alpaca_connectors__bars_1d_sip_all`
  - `alpaca_connectors__bars_1d_iex_raw`
  - `alpaca_connectors__acct_alpaca`
- `msm.start_engine(...)` successfully resolved and attached all three project models plus
  `AssetTable`, `AccountGroupTable`, and `AccountTable` foreign-key dependencies.
- The daily job workflow under `.mainsequence/workflows/` validated against backend workflow
  contract `2.1.0` with no errors or warnings.
- Phase 0 was synchronized to `main` as commit
  `a40ac29b93f23c49315e207c7b851dbdf0fc1dc5` with tag `v0.1.20`.
- The backend reconciled exactly one CodeRepository Job from the existing workflow:
  - Job UID: `45defea5-3ffe-473b-a6b8-22da1e4acb5f`
  - execution path: `src/jobs/run_daily_stock_bars_holdings_ivv.py`
  - schedule: `0 0 * * *`
  - commit: `a40ac29b93f23c49315e207c7b851dbdf0fc1dc5`
  - image status: `ready`
- The Job's exact Python 3.13 image is ready and verified:
  - image UID: `cc378d66-77df-4f8e-a66e-b4b678feabe7`
  - output digest: `sha256:96033c10577e3aa521b96dad5a3f72edb410e29b30d6ed627434c9626a6b0dc4`
  - source archive SHA-256:
    `feabe099703948f6cb1cbc4837b52ba5dac385c09d7f2538f5f45f82c0f551dc`
- No additional Jobs were declared, created, or run during Phase 0.

## Pending

- The new bars tables are empty until a controlled first update/backfill is executed.
- Live Alpaca account registration and holdings publication require explicit real-account
  authorization and were not executed as part of the schema migration.
- `etfhextractor 0.4.1` is published at commit
  `036c8ba7f625f45dcb58e909ebf4eebbedbb5b97` with tag `v0.4.1`. This checkout's lock and installed
  environment resolve that release while `pyproject.toml` retains an unpinned git source.

## Validation

- External `etfhextractor` full suite: 86 passed.
- This project's full integration suite against the installed, locked external release: 69 passed
  with two third-party deprecation warnings.
- Ruff import/lint selection: passed.
- `uv lock --check`: passed.
- `uv pip check`: all installed packages compatible.
- `mkdocs build --strict`: passed.
- Live iShares IVV extraction through the migrated dependency returned 508 holdings dated
  2026-08-31.
