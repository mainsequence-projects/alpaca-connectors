# Alpaca Connectors FastAPI Application

This package is the HTTP integration layer over the reusable `alpaca_connectors` services.

Routers expose Project State, Assets, Accounts, Holdings, order Execution (ADR 0016), Universe
Sources, materialized Universes, Market Data datasets, and stored Alpaca bar configurations. Pydantic transport contracts live in
`schemas.py`; provider-neutral collection, discovery, bulk-action, error, and observable-operation
contracts derive from `msm.api.http`; route-facing response shaping lives in `services/`. Provider
extraction, MetaTable writes, account transformation, source resolution, and DataNode execution
remain in the reusable `alpaca_connectors` modules.

`main.py` builds the application in `create_app()`, which installs the Main Sequence request
identity once (`mainsequence.server.fastapi.install_request_identity`); the platform's FastAPI
launcher refuses to serve an application without it. Every route needs a verified caller: the
platform-signed caller assertion when hosted, a direct Bearer token checked against
`MAINSEQUENCE_ENDPOINT` locally. Routes read the caller only through `identity.py`
(`User.get_logged_user()`), never from `request.state` or a header. See
[ADR 0014](../../../../docs/adrs/0014_sdk_request_identity.md).

Run locally, then call it with a token from the same backend:

```bash
uv run uvicorn alpaca_connectors.api.app.main:app --port 8321 --reload
curl -H "Authorization: Bearer $(.venv/bin/mainsequence auth token)" http://127.0.0.1:8321/health
```

Deployment is declared in `.mainsequence/workflows/alpaca-connectors-api.yaml`.
