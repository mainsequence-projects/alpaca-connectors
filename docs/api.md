# API

## Goal

The project includes a FastAPI surface for automating:

- asset discovery
- asset registration
- holdings-category discovery
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

### Preview asset registration

```text
POST /v1/assets/registration/plan
```

Body supports either:

- `symbols`
- or `seed_tickers` with `component_provider`

This endpoint does not write assets.

### Execute asset registration

```text
POST /v1/assets/registration/execute
```

This runs the FIGI-based registration flow and creates missing MainSequence public assets.

### Preview holdings category sync

```text
POST /v1/holdings-categories/plan
```

This extracts ETF holdings and reports blockers without writing anything.

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
