# API

## Goal

The project includes a thin FastAPI surface for automating:

- discovery configuration
- asset registration
- holdings-category synchronization

Main files:

- `api/app/main.py`
- `api/app/schemas.py`
- `api/app/services.py`

## Design

Route handlers validate input and return application-owned response models. Service functions call
the reusable implementation under `src/`; the API does not duplicate Alpaca, OpenFIGI, ETF
extraction, or category logic.

The FastAPI lifespan attaches the ms-markets runtime once through
`src.runtime.start_markets_engine()` so shared asset and category services resolve the migrated
backend tables correctly.

## Endpoints

### Health

```text
GET /health
```

Returns a minimal readiness payload.

### Discovery configuration

```text
GET /v1/discovery/config
```

Returns supported component providers, the normalized ETF provider map, and configured seed
universes.

### Execute asset registration

```text
POST /v1/assets/registration/execute
```

Runs the strict Alpaca and OpenFIGI registration flow and creates missing Main Sequence public
assets. Callers may provide exact symbols or expand ETF seed tickers through a supported holdings
provider.

### Execute holdings-category synchronization

```text
POST /v1/holdings-categories/execute
```

Creates or refreshes the holdings-backed `AssetCategory` after every extracted constituent resolves
unambiguously to a registered Main Sequence asset.

## Local Run

```bash
uv run uvicorn api.app.main:app --reload
```

## Ownership Boundary

ETF extraction is owned by the external `etfhextractor` package and consumed through
`src/etf_holdings.py`. Alpaca registration, holdings orchestration, and Alpaca bars remain owned by
the reusable modules under `src/`. The API is only an HTTP layer over those services.
