# Project Record

## Stable Paths

- Repository: `/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153`
- Project instructions: `AGENTS.md`
- Project CLI package: `src/cli/`
- Scheduled-job launcher path: `src/jobs/run_daily_stock_bars_holdings_ivv.py`
- FastAPI app: `api/app/main.py`
- API schemas: `api/app/schemas.py`
- API services: `api/app/services.py`
- API docs: `docs/api.md`
- Agent docs: `docs/agent.md`
- Agent card: `.agents/agent_card.json`
- Project-specific custom skills root: `.agents/skills/`
- ETF extraction docs: `docs/etf_extraction.md`
- Workspace payload: `command_center/workspaces/alpaca_assets_registry.workspace.yaml`

## Stable Project Context

- Main Sequence project ID: `153`
- Python: `3.11.5` as reported by `mainsequence project current --debug`
- Local SDK: `3.18.19` as reported by `mainsequence project current --debug`
- Latest GitHub SDK reported by `mainsequence project current --debug` on 2026-04-28: `3.18.20`

## Useful Commands

```bash
/bin/zsh -lc 'set -a; source .env; export MAINSEQUENCE_AUTH_MODE=jwt; set +a; .venv/bin/mainsequence project current --debug'
/bin/zsh -lc 'set -a; source .env; export MAINSEQUENCE_AUTH_MODE=jwt; set +a; .venv/bin/mainsequence project refresh_token --path .'
.venv/bin/mainsequence project update AGENTS.md --path .
.venv/bin/alpaca-connectors asset register --help
.venv/bin/alpaca-connectors holdings-category create --help
.venv/bin/alpaca-connectors bars run --help
.venv/bin/alpaca-connectors asset IVV update_prices daily --help
.venv/bin/python -m unittest tests.test_api_app
.venv/bin/python -m unittest tests.test_cli tests.test_run_daily_stock_bars tests.test_alpaca_bars_support
.venv/bin/python -m unittest discover tests
```

## CLI Notes

- The installed console script name is `alpaca-connectors`.
- Asset registration is exposed as `alpaca-connectors asset register`.
- Holdings category creation is exposed as `alpaca-connectors holdings-category create`.
- Generic stock-bar execution is exposed as `alpaca-connectors bars run`.
- Single-asset shorthand stock-bar execution is exposed as `alpaca-connectors asset <ticker> update_prices <period>`.
- The shorthand asset price-update parser accepts both `update_prices` and `update-prices`; use
  `update_prices` in repo docs and examples.
- The legacy script entry points for those workflows were removed on 2026-04-28.
- The local project-to-agent surface is metadata-and-skill based; no standalone local `agent.py`
  runtime is assumed by default.
