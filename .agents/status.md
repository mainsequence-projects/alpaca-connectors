# Project Status

## Verified

- Repository path is `/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153`.
- Main Sequence project detection reports project ID `153`.
- `mainsequence project current --debug` completed successfully on 2026-04-28 and reported Python `3.11.5`, project ID `153`, local SDK `3.18.19`, and latest GitHub SDK `3.18.20`.
- `mainsequence project refresh_token --path .` completed successfully on 2026-04-20.
- Asset registration now runs through the installed CLI entry point `alpaca-connectors asset register`.
- Holdings category creation now runs through the installed CLI entry point `alpaca-connectors holdings-category create`.
- Daily stock-bar updates now run through the installed CLI entry points `alpaca-connectors bars run` and `alpaca-connectors asset <ticker> update_prices <period>`.
- `AGENTS.md` now contains project-specific instructions for the Alpaca registration,
  holdings-category, and stock-bar CLI workflows.
- Legacy scripts `scripts/register_asset.py` and `scripts/create_holdings_category.py` have been removed.
- Legacy scripts `scripts/run_daily_stock_bars.py` and `scripts/run_daily_stock_bars_holdings_ivv.py` have been removed.
- Scheduled job execution for the IVV daily bars preset now points to `src/jobs/run_daily_stock_bars_holdings_ivv.py`.
- Project docs now reference the CLI entry points instead of the deleted scripts.
- Project docs now include a standalone ETF extraction architecture page at `docs/etf_extraction.md`.
- `uv sync` rebuilt and reinstalled the local package on 2026-04-28 after the CLI entry-point change.
- `.venv/bin/alpaca-connectors asset register --help` completed successfully on 2026-04-28.
- `.venv/bin/python -m src.cli asset IVV update_prices daily --help` completed successfully on 2026-04-28.
- `.venv/bin/alpaca-connectors asset IVV update_prices daily --help` completed successfully on 2026-04-28.
- `.venv/bin/python -m unittest tests.test_cli tests.test_run_daily_stock_bars tests.test_alpaca_bars_support` passed 12 tests on 2026-04-28.

## Pending

- The new CLI entry points were validated locally only; no live asset registration or holdings-category sync was executed in this turn.
- A live `alpaca-connectors asset IVV update_prices daily --plan-only` check failed on 2026-04-28 because JWT refresh and MainSequence secret lookup for `ALPACA_API_KEY` failed in this shell.
- The local SDK is one version behind the latest GitHub SDK reported by `mainsequence project current --debug`.
- Native `mainsequence project update AGENTS.md --path .` currently needs a workaround in this environment because the installed `agent_scaffold/AGENTS.md` template triggers the CLI managed-block parser error.

## Notes

- Running `python -m src.cli` with no subcommand prints help and exits non-zero by design.
- The standalone ETF extraction docs page is documentation-only; no new live platform verification was required for that change.
- The AGENTS scaffold update succeeded on 2026-04-28 only after forcing the CLI to import a
  temporary cleaned `agent_scaffold/AGENTS.md` through `PYTHONPATH`.
