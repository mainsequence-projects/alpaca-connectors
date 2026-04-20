# API

## Goal

The project includes a FastAPI surface for automating:

- asset discovery
- asset registration
- single-ticker registration for Command Center AppComponents
- lightweight charts OHLC spec generation for Command Center rendering
- holdings-category synchronization

Main files:

- `api/app/main.py`
- `api/app/schemas.py`
- `api/app/services.py`

## Design

The API is intentionally thin.

- route handlers validate input and return typed responses
- service functions call the existing logic under `src/`
- the API does not rebuild the Alpaca, FIGI, extractor, or category workflows

## Endpoints

### Health

```text
GET /health
```

### Discovery configuration

```text
GET /v1/discovery/config
```

Returns:

- supported component providers
- ETF provider map
- configured seed universes

### Execute asset registration

```text
POST /v1/assets/registration/execute
```

This runs the FIGI-based registration flow and creates missing MainSequence public assets.

### AppComponent single-ticker registration

```text
POST /v1/app-components/assets/register-ticker
```

This is the AppComponent-facing operation for registering exactly one asset by ticker.

It intentionally uses the default generated AppComponent form instead of a custom editable form.

### Lightweight Charts OHLC payload

```text
POST /v1/charts/lightweight/ohlc
```

This endpoint has two modes.

Command Center selector mode:

- `ticker` query parameter only

Selector mode returns `items` and `pagination` for the AppComponent `select2` async ticker search.
Each item exposes a user-facing ticker `label`, a platform `unique_identifier`, and display text.

Chart mode accepts Command Center query parameters only.

Command Center query fields:

- `ticker`, containing the selected ticker, FIGI, or asset unique identifier
- `start_date`
- `end_date`

In Command Center query mode, the selected `ticker` value is resolved strictly inside the
configured `AssetCategory` before bars are queried. The API uses the fixed OHLC DataNode
identifier `alpaca_stock_bars_1d_sip_all`; the form does not expose DataNode or unique
identifier inputs.

Returns:

- structured `spec` object matching the `lightweight-charts-spec` widget format
- `spec_json` string for direct widget-props usage

### Execute holdings category sync

```text
POST /v1/holdings-categories/execute
```

This creates or refreshes the holdings `AssetCategory` after strict validation passes.

## Local Run

```bash
uv run uvicorn api.app.main:app --reload
```

## Important Boundary

The extractor logic, registration logic, and holdings-category logic remain owned by `src/`.
The API is only the HTTP contract over those services.

This API does not add `LoggedUserContextMiddleware` because the current routes do not consume request-local MainSequence user context.
