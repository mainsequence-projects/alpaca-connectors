# Journal

## 2026-09-02

- Context: migrate the connector and its backend-owned project MetaTables from the old SDK stack
  to Main Sequence SDK 8 and ms-markets 1 while keeping dependency declarations compatible rather
  than exactly pinned.
- Dependency work: moved the runtime to Python 3.13, set `mainsequence>=8.0.7` and
  `ms-markets>=1.0.2`, rebuilt `uv.lock`, exported `requirements.txt`, and refreshed the installed
  SDK and ms-markets scaffold bundles.
- Code work: migrated the Alpaca bars node to `_required_output_table()`, changed API startup to a
  lifespan handler, migrated `etfhextractor` itself and delegated the Alpaca portfolio graph back to its reusable
  builder without changing the Alpaca portfolio identity, and replaced `scheduled_jobs.yaml` with
  a backend-validated CodeRepository workflow document.
- Backend work: restored CLI authentication, resolved the CodeRepository branch, generated and
  reviewed Alembic revision `0001`, corrected the provider's foreign-key metadata closure, applied
  the revision, and finalized the registry plus three project tables. Verification returned
  `0001 (head)`, four active resources, and zero reserved/failed resources; ms-markets runtime
  attachment then resolved all project models and transitive core dependencies.
- Scheduling state: the new workflow validated against backend contract `2.1.0`, but the backend
  job list is empty until a reviewed commit/sync triggers repository processing. The dirty checkout
  was not committed or pushed automatically.
- Dependency backend work: applied the `etfhextractor_migrations:migration` provider at `0001`,
  finalized its registry and demo-bars output active, attached its runtime, and live-read 508 IVV
  holdings from iShares. Its full suite passed 86 tests; this project passed 83 integration tests
  against the migrated checkout. After explicit authorization, canonical CodeRepository sync
  published commit `036c8ba` and tag `v0.4.1`; the Alpaca lock and environment now resolve that
  remote release without a local override.
- Presentation cleanup: removed the retired browser workspace integration, generated documentation
  output, and legacy dashboard stub. The remaining FastAPI routes now return application-owned
  response models for discovery, registration, and holdings synchronization. The reduced project
  suite passes 69 tests.
- Pending: controlled first bars backfill; live Alpaca account registration; and an exact-image job
  release.

## 2026-05-08

- Context: prepare the repository for local project-to-agent use without introducing a standalone
  local `agent.py` runtime.
- Work completed: replaced the `AGENTS.md` placeholder with real project-specific instructions,
  added project-specific custom skills under `.agents/skills/`, created `.agents/agent_card.json`,
  added `docs/agent.md`, updated `README.md` and `mkdocs.yml`, added local validation coverage in
  `tests/test_agent_artifacts.py`, and removed the mistaken `google-adk` dependency that had been
  introduced during an earlier mis-scoped attempt.
- Verification: the work was validated by repository inspection and local artifact consistency
  design only; no live platform validation or test execution was performed in this turn.

## 2026-05-08

- Context: make the repository README useful as the project entrypoint instead of mostly referring
  readers to deeper docs pages.
- Work completed: rewrote `README.md` into a capability summary covering the supported CLI, API,
  ETF extraction, DataNode, and job surfaces, plus the repo ownership boundary
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
