# Project Record

## Stable Paths

- Repository: `/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153`
- FastAPI app: `api/app/main.py`
- API schemas: `api/app/schemas.py`
- API services: `api/app/services.py`
- API docs: `docs/api.md`
- Workspace payload: `command_center/workspaces/alpaca_assets_registry.workspace.yaml`

## Stable Project Context

- Main Sequence project ID: `153`
- Python: `3.11.5` as reported by `mainsequence project current --debug`
- Local SDK: `3.18.8` as reported by `mainsequence project current --debug`

## Useful Commands

```bash
/bin/zsh -lc 'set -a; source .env; export MAINSEQUENCE_AUTH_MODE=jwt; set +a; .venv/bin/mainsequence project current --debug'
/bin/zsh -lc 'set -a; source .env; export MAINSEQUENCE_AUTH_MODE=jwt; set +a; .venv/bin/mainsequence project refresh_token --path .'
.venv/bin/python -m unittest tests.test_api_app
.venv/bin/python -m unittest discover tests
```

## API Contract Notes

- `GET /health` returns `HealthResponse`.
- `GET /v1/discovery/config` returns `DataNodeTableSourceInputResponse`.
- `POST /v1/assets/registration/execute` returns `DataNodeTableSourceInputResponse`.
- `POST /v1/app-components/assets/register-ticker` returns `AssetRegistrationByTickerResponse`.
- `POST /v1/holdings-categories/execute` returns `DataNodeTableSourceInputResponse`.
- `POST /v1/charts/lightweight/ohlc` returns `LightweightOhlcResponse` with structured `spec` only.
