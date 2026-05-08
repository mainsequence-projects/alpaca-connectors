# alpaca-connectors

`alpaca-connectors` is a Main Sequence project for onboarding Alpaca US equities, turning ETF
holdings into reusable Main Sequence asset universes, and publishing Alpaca stock bars into
platform datasets and UI surfaces.

## Capability Summary

- Register Alpaca US equity assets as Main Sequence public assets through strict OpenFIGI
  resolution.
- Expand ETF seed tickers into component symbols from official provider sources.
- Build holdings-backed `AssetCategory` universes such as `HOLDINGS__IVV`.
- Publish Alpaca OHLCV bars through a reusable `DataNode` for either one registered asset or a
  category universe.
- Expose thin FastAPI endpoints for registration, holdings sync, discovery, and lightweight OHLC
  chart payloads.
- Generate Command Center workspace/AppComponent payloads for asset registration and chart
  loading.
- Run recurring ETF maintenance and scheduled daily bar updates from repository-managed job
  entrypoints.

## Supported Operator Surfaces

- `alpaca-connectors asset register`: plan or execute strict Alpaca + FIGI-backed asset
  registration.
- `alpaca-connectors holdings-category create`: plan or execute holdings `AssetCategory` creation
  from ETF constituents.
- `alpaca-connectors bars run`: run the stock-bars `DataNode` for a holdings category or explicit
  ticker list.
- `alpaca-connectors asset <ticker> update_prices <period>`: shorthand single-asset stock-bar
  update with project defaults `feed=sip` and `adjustment=all`.
- `uv run uvicorn api.app.main:app --reload`: run the FastAPI surface locally.
- `src/jobs/run_daily_stock_bars_holdings_ivv.py` and
  `src/jobs/run_etf_maintenance_routines.py`: repository-local job entrypoints used by scheduled
  execution.

## Workflow Highlights

- Asset registration is strict. A symbol must exist in Alpaca, resolve to a FIGI, and not already
  exist in Main Sequence by FIGI before it is created.
- ETF extraction is provider-owned. `etf_extraction/` expands holdings and plans category
  membership; Alpaca code in `src/` consumes those outputs later.
- Holdings categories are strict. Category sync refuses to run when extracted symbols are missing
  or ambiguous in Main Sequence.
- Bars publishing is reusable. The `AlpacaStockBarsNode` publishes asset-indexed OHLCV tables
  keyed by `frequency_id`, `feed`, and `adjustment`.
- The single-asset shorthand and the generic bars runner intentionally differ in defaults. The
  shorthand uses `sip/all`; generic `bars run` defaults to `iex/raw` unless you set flags
  explicitly.

## API And UI Surfaces

- `GET /health`: basic API health.
- `GET /v1/discovery/config`: supported component providers plus configured ETF seed/provider
  metadata.
- `POST /v1/assets/registration/execute`: execute multi-symbol or ETF-seed-backed asset
  registration.
- `POST /v1/app-components/assets/register-ticker`: single-ticker registration for Command Center
  AppComponents.
- `POST /v1/holdings-categories/execute`: execute holdings-category sync after strict validation
  passes.
- `POST /v1/charts/lightweight/ohlc`: async asset selector and lightweight-charts OHLC payload
  builder.
- `src/command_center/workspaces.py` and
  `command_center/workspaces/alpaca_assets_registry.workspace.yaml`: Command Center workspace
  builders/artifacts for register-by-ticker and chart widgets.

## Supported ETF Providers

- `ishares`
- `invesco`
- `vanguard`
- `state_street`

## Repository Boundaries

- `src/`: Alpaca-owned registration, bar execution, CLI, jobs, and Command Center workspace
  builders.
- `etf_extraction/`: ETF provider extraction, ETF settings, seed expansion, and holdings-category
  planning/sync.
- `api/`: thin FastAPI contracts over reusable project services.
- `docs/`: detailed workflow docs and operational notes.
- `tests/` and `etf_extraction/tests/`: regression coverage for CLI, API, ETF extraction, and bar
  support logic.

## Runtime Prerequisites

- Live platform actions need an authenticated Main Sequence session.
- Alpaca credentials are resolved from environment variables first, then from Main Sequence
  secrets.
- Browser-backed ETF extraction flows require the Playwright browser runtime.

## Quickstart

```bash
uv sync
uv run mkdocs serve
uv run uvicorn api.app.main:app --reload
```

## Common Commands

```bash
alpaca-connectors asset register --symbols NVDA,AAPL
alpaca-connectors asset register --seed-tickers IVV --component-provider ishares
alpaca-connectors holdings-category create --etf-ticker IVV
alpaca-connectors bars run --asset-category-unique-identifier HOLDINGS__IVV --frequency-id 1d --feed sip --adjustment all
alpaca-connectors asset IVV update_prices daily --plan-only
```

## Detailed Docs

Use the pages under `docs/` for workflow depth after this summary:

- `docs/etf_extraction.md`
- `docs/assets/registration.md`
- `docs/assets/holdings_categories.md`
- `docs/data_nodes/alpaca_bars.md`
- `docs/api.md`
- `docs/command_center/app_component.md`
- `docs/operations/jobs.md`
