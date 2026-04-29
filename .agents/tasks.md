# Tasks

## Open

### Upgrade SDK And Recheck Native AGENTS Scaffold Update

- Owning skill: `.agents/skills/maintenance/bug_auditor/SKILL.md`
- Scope: upgrade the local Main Sequence SDK to the latest available version, then rerun
  `mainsequence project update AGENTS.md --path .` without the temporary `PYTHONPATH` workaround.
- Expected output: confirmation that the native scaffold-update command works on the upgraded SDK,
  or a preserved minimal reproducer showing the installed `agent_scaffold/AGENTS.md` parser issue
  still exists.
- Validation evidence: either a successful native `mainsequence project update AGENTS.md --path .`
  run or the captured `Installed agent_scaffold AGENTS.md must contain exactly one Main Sequence
  managed block.` failure on the upgraded SDK.

### Refresh Credentials And Live-Verify Asset Price Update CLI

- Owning skill: `.agents/skills/data_publishing/data_nodes/SKILL.md`
- Scope: refresh MainSequence credentials in the active shell, then run `alpaca-connectors asset IVV update_prices daily --plan-only` end to end.
- Expected output: confirmation that the shorthand CLI resolves the registered IVV asset, builds the `AlpacaStockBarsConfig`, and prints the planned DataNode summary without auth or secret lookup failures.
- Validation evidence: successful command output including the resolved asset sample and no `JWT refresh failed` or `Failed to retrieve MainSequence secret 'ALPACA_API_KEY'` error.

### Publish Updated FastAPI Response Contract

- Owning skill: `.agents/skills/platform_operations/orchestration_and_releases/SKILL.md`
- Scope: create or update the FastAPI project resource release after the `/v1/charts/lightweight/ohlc` response-model change is committed and synced.
- Expected output: a new FastAPI `ResourceRelease` for the current API code, with the relevant Command Center workspace/AppComponent targeting that release if needed.
- Validation evidence: `mainsequence project project_resource list --filter resource_type=fastapi`, the created release id, and a successful `/openapi.json` check showing the chart route has a `200` response body schema referencing `LightweightOhlcResponse` with structured `spec` only.

### Recheck Live Command Center AppComponent Contract

- Owning skill: `.agents/skills/command_center/app_components/SKILL.md`
- Scope: after the FastAPI release is updated, verify the live AppComponent sees response schemas for `POST /v1/charts/lightweight/ohlc`.
- Expected output: confirmation that OpenAPI discovery exposes only `ticker`, `start_date`, `end_date` inputs and the single `LightweightOhlcResponse` response body with structured `spec` only.
- Validation evidence: registry/workspace inspection plus live AppComponent behavior or a captured `/openapi.json` response from the released API.

## Completed

### Add Project-Specific Alpaca Workflow Instructions To AGENTS.md

- Owning skill: `.agents/skills/project_builder/SKILL.md`
- Scope: replace the scaffold placeholder in `AGENTS.md` with the repository-specific rules for
  Alpaca asset registration, holdings-category creation, and stock-bar updates, including the
  supported CLI entry points.
- Expected output: future agents can read `AGENTS.md` and find the supported commands, dry-run
  behavior, operational prerequisites, and repository boundary rules for these workflows.
- Validation evidence: `AGENTS.md` contains project-specific instructions for
  `alpaca-connectors asset register`, `alpaca-connectors holdings-category create`,
  `alpaca-connectors bars run`, and `alpaca-connectors asset <ticker> update_prices <period>`.

### Move Daily Stock Bars Into The Project CLI

- Owning skill: `.agents/skills/data_publishing/data_nodes/SKILL.md`
- Scope: replace the standalone stock-bar runner scripts with project CLI commands and keep a repository-local scheduled-job launcher for the IVV preset.
- Expected output: `alpaca-connectors bars run` and `alpaca-connectors asset <ticker> update_prices <period>` both exist, docs point at them, and scheduled jobs use a non-`scripts/` launcher path.
- Validation evidence: `.venv/bin/python -m unittest tests.test_cli tests.test_run_daily_stock_bars tests.test_alpaca_bars_support` passed on 2026-04-28; both help commands for the shorthand path succeeded.

### Add Response Body Model To Chart API Route

- Owning skill: `.agents/skills/application_surfaces/api_surfaces/SKILL.md`
- Scope: make the schema-visible chart route declare one typed response model and return that Pydantic model directly.
- Expected output: local OpenAPI contains a single response body schema for selector and chart modes, with structured `spec` only.
- Validation evidence: `python -m unittest tests.test_api_app` and `python -m unittest discover tests` passed on 2026-04-20.
