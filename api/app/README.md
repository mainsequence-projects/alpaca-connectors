# Alpaca Connectors API

This FastAPI app exposes thin HTTP endpoints for:

- asset registration execution
- lightweight-charts OHLC spec generation plus AppComponent ticker search
- holdings-category synchronization

The API does not rebuild the producer logic. It delegates to the existing services in `src/`.

Main entrypoint:

- `api/app/main.py`

Main service layer:

- `api/app/services.py`

Request and response models:

- `api/app/schemas.py`
