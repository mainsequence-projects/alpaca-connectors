# Journal

## 2026-05-08

- Context: make the repository README useful as the project entrypoint instead of mostly referring
  readers to deeper docs pages.
- Work completed: rewrote `README.md` into a capability summary covering the supported CLI, API,
  Command Center, ETF extraction, DataNode, and job surfaces, plus the repo ownership boundary
  between `src/` and `etf_extraction/`.
- Verification: documentation content was verified by inspection against the current code,
  docs, and project-state files; no live platform validation was required because the change was
  documentation-only.

## 2026-04-30

- Context: make ETF extraction documented independently from Alpaca-specific pages.
- Work completed: added the standalone architecture page `docs/etf_extraction.md`, linked it from `mkdocs.yml`, `docs/index.md`, `README.md`, and added cross-references from the ETF extractor and holdings-category docs.
- Verification: documentation wiring was verified locally by inspecting the MkDocs navigation config and the updated repository doc entry points; no live platform validation was required because the change was documentation-only.

## 2026-04-28

- Context: replace the scaffold placeholder in `AGENTS.md` with project-specific Alpaca workflow
  instructions.
- Work completed: updated `AGENTS.md` so the project-specific section now documents the supported
  `alpaca-connectors` commands for asset registration, holdings-category creation, single-asset
  price updates, generic bars runs, and the repo rule that these workflows should stay in reusable
  `src/` modules rather than new standalone scripts.
- Verification: confirmed `project_builder/SKILL.md` still matches the installed scaffold copy;
  `mainsequence project current --debug` succeeded and reported project `153`, Python `3.11.5`,
  local SDK `3.18.19`, and latest GitHub SDK `3.18.20`.
- SDK issue: native `mainsequence project update AGENTS.md --path .` failed with `Installed
  agent_scaffold AGENTS.md must contain exactly one Main Sequence managed block.` because the
  installed scaffold template contains extra inline marker strings outside the real managed block;
  the update succeeded only through a temporary `PYTHONPATH` override that pointed the CLI at a
  cleaned scaffold copy.

- Context: move the daily stock-bar runner into the project CLI and add a shorthand single-asset price update command.
- Work completed: added `src/cli/bars.py`, added `alpaca-connectors bars run` plus the shorthand `alpaca-connectors asset <ticker> update_prices <period>`, moved the scheduled IVV preset launcher to `src/jobs/run_daily_stock_bars_holdings_ivv.py`, removed the old stock-bar scripts from `scripts/`, updated docs and `scheduled_jobs.yaml`, and added CLI/data-node regression tests.
- Verification: `.venv/bin/python -m unittest tests.test_cli tests.test_run_daily_stock_bars tests.test_alpaca_bars_support` passed 12 tests; `.venv/bin/python -m src.cli asset IVV update_prices daily --help` and `.venv/bin/alpaca-connectors asset IVV update_prices daily --help` both succeeded.
- Blocker: a live `alpaca-connectors asset IVV update_prices daily --plan-only` run failed because JWT refresh returned `401` and the CLI could not retrieve the MainSequence secret `ALPACA_API_KEY` in the current shell.

- Context: move asset registration and holdings-category creation out of standalone scripts and into a project CLI under `src/cli`.
- Work completed: added `src/cli` with nested argparse commands for `asset register` and `holdings-category create`, added a console-script entry point in `pyproject.toml`, removed `scripts/register_asset.py` and `scripts/create_holdings_category.py`, updated repo docs to reference the CLI, and added CLI regression tests.
- Verification: `uv sync` rebuilt and reinstalled the package; `.venv/bin/alpaca-connectors asset register --help` succeeded; `.venv/bin/python -m unittest tests.test_cli tests.test_holdings_categories` passed 5 tests.
- Note: `python -m src.cli` without a subcommand prints help and exits non-zero because the CLI requires an explicit command path.

## 2026-04-20

- Context: align the FastAPI API surface with the Main Sequence standard that schema-visible endpoints should have explicit response bodies and response models.
- Work completed: synced root `AGENTS.md` to the canonical scaffold, checked the Main Sequence FastAPI and Command Center contract docs, corrected `POST /v1/charts/lightweight/ohlc` to use the single `LightweightOhlcResponse` response model, updated API docs, and added regression tests for response-model coverage and the chart route OpenAPI response schema.
- Correction: removed the duplicate stringified spec field from the OHLC chart API contract after confirming the workspace path consumes structured JSON.
- Verification: `mainsequence project current --debug` detected project `153`; `mainsequence project refresh_token --path .` succeeded; `python -m unittest tests.test_api_app` passed 12 tests; `python -m unittest discover tests` passed 45 tests.
- Follow-up: create a new FastAPI release and recheck the live Command Center AppComponent/OpenAPI discovery once the code is synced and released.
