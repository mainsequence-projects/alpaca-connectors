# Project Record

## Stable paths

- Repository:
  `/Users/jose/mainsequence-dev/main-sequence-workbench/projects/alpacaconnectors-945bfddd-5f1f-4541-a87a-faea3af6271f`
- Project instructions: `AGENTS.md`
- Project CLI: `src/cli/`
- Reusable implementation: `src/`
- FastAPI application: `api/app/main.py`
- Project migration provider: `src.migrations:migration`
- Migration revisions: `src/migrations/versions/alpaca_connectors/`
- Repository workflows: `.mainsequence/workflows/`
- Scheduled launcher: `src/jobs/run_daily_stock_bars_holdings_ivv.py`
- Documentation map: `docs/SUMMARY.md`
- Project state: `.agents/status.md`, `.agents/tasks.md`, `.agents/journal.md`

## Platform context

- CodeRepository UID: `c4980dd0-db33-420c-9a3f-050815a03512`
- CodeRepositoryBranch UID: `945bfddd-5f1f-4541-a87a-faea3af6271f`
- Branch: `main`
- Python: `3.13.11`
- Main Sequence SDK: `8.0.7`
- ms-markets: `1.0.2`
- Migration namespace: `alpaca-connectors`
- Alembic registry table: `alpaca_connectors__alembic_version`
- Alembic head: `0001`

## Project-owned backend tables

- SIP/all daily bars: `alpaca_connectors__bars_1d_sip_all`
- IEX/raw daily bars: `alpaca_connectors__bars_1d_iex_raw`
- Alpaca account details/current financials: `alpaca_connectors__acct_alpaca`

Core assets, accounts, holdings, calendars, and portfolio models remain owned by ms-markets.

## Useful commands

```bash
mainsequence login
mainsequence code-repository refresh-token --path .
mainsequence code-repository current --debug
mainsequence code-repository update-sdk --path .
mainsequence code-repository update AGENTS.md --path .
mainsequence code-repository update-agent-skills --path .
mainsequence migrations current --provider src.migrations:migration
mainsequence migrations revision --provider src.migrations:migration -m "describe change"
mainsequence migrations upgrade --provider src.migrations:migration head
mainsequence code-repository jobs list --path .
alpaca-connectors asset register --help
alpaca-connectors holdings-category create --help
alpaca-connectors bars run --help
alpaca-connectors asset IVV update_prices daily --plan-only
alpaca-connectors account register --help
uv run pytest
uv run ruff check src api tests
uv run mkdocs build --strict
```

## Contracts

- Asset registration requires an Alpaca match and OpenFIGI match before public-asset creation.
- Holdings category sync fails when a constituent is unresolved or ambiguous.
- DataNode schema lives on `_required_output_table()` storage classes.
- Runtime attachment expects migrations to be complete and never creates schema.
- Alpaca owns its bars/interpolation price source and configuration-derived Portfolio identity;
  `etfhextractor.build_etf_tracking_portfolio` owns calendar, signal, Portfolio row, configuration,
  and `PortfoliosDataNode` assembly.
- Long-lived schedules live under `.mainsequence/workflows/`; `scheduled_jobs.yaml` is removed.
- Dependency lower bounds live in `pyproject.toml`; reproducibility lives in `uv.lock` and exported
  `requirements.txt`.
