# Alpaca Connectors FastAPI Application

This package is the HTTP integration layer over the reusable `alpaca_connectors` services.

Routers expose Project State, Assets, Accounts, Holdings, Universe Sources, materialized Universes,
Market Data datasets, and stored Alpaca bar configurations. Pydantic transport contracts live in
`schemas.py`; provider-neutral collection, discovery, bulk-action, error, and observable-operation
contracts derive from `msm.api.http`; route-facing response shaping lives in `services/`. Provider
extraction, MetaTable writes, account transformation, source resolution, and DataNode execution
remain in the reusable `alpaca_connectors` modules.

Main Sequence injects `request.state.user` and `request.state.user_uid` in deployed requests. The
application does not accept a browser-supplied user identity and does not install SDK middleware.

Run locally:

```bash
uv run uvicorn alpaca_connectors.api.app.main:app --reload
```

Deployment is declared in `.mainsequence/workflows/alpaca-connectors-api.yaml`.
