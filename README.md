# alpaca-connectors

`alpaca-connectors` is a Main Sequence project for onboarding Alpaca US equities, turning ETF
holdings into reusable Main Sequence asset universes, and publishing Alpaca stock bars into
platform datasets.

## Capability Summary

- Register Alpaca US equity assets as Main Sequence public assets through strict OpenFIGI
  resolution.
- Expand ETF seed tickers into component symbols from official provider sources.
- Build holdings-backed `AssetCategory` universes such as `HOLDINGS__IVV`.
- Build ETF-tracking ms-markets portfolios from ETF holdings signals and interpolated Alpaca bars.
- Publish Alpaca OHLCV bars through a reusable `DataNode` for either one registered asset or a
  category universe.
- Expose thin FastAPI endpoints for registration, holdings sync, and discovery.
- Run ETF maintenance manually and publish the one existing repository-managed scheduled daily
  bar Job.

## Agentic Capabilities

This repository is now prepared for project-to-agent use through local project metadata and skills.

- `AGENTS.md` documents the real project-specific workflows and the supported agent capability
  boundary.
- `.agents/skills/` contains project-specific workflow skills for asset registration,
  holdings-category sync, stock-bar operations, and the thin FastAPI surface.
- `.agents/agent_card.json` describes the local agent-ready capability surface for this project.

Important boundary:

- this repo does not currently claim a standalone local `agent.py` runtime
- the agent-facing action surface is the existing CLI, reusable modules, and API contracts already
  present in the project

## Supported Operator Surfaces

- `alpaca-connectors asset register`: plan or execute strict Alpaca + FIGI-backed asset
  registration.
- `alpaca-connectors holdings-category create`: plan or execute holdings `AssetCategory` creation
  from ETF constituents.
- `alpaca-connectors account register`: register an Alpaca trading account into ms-markets and
  snapshot its balances + holdings (`--paper`/`--no-paper`, `--plan-only`). See
  `docs/account/registration.md`.
- `alpaca-connectors bars run`: run the stock-bars `DataNode` for a holdings category or explicit
  ticker list.
- `alpaca-connectors asset <ticker> update_prices <period>`: shorthand single-asset stock-bar
  update with project defaults `feed=sip` and `adjustment=all`.
- `uv run uvicorn api.app.main:app --reload`: run the FastAPI surface locally.
- `src/jobs/run_daily_stock_bars_holdings_ivv.py`: launcher for the one repository-managed
  scheduled Job.
- `src/jobs/run_etf_maintenance_routines.py`: manual routine runner; it is not declared as a
  backend Job.
- `.mainsequence/workflows/daily-stock-bars-holdings-ivv.yaml`: the backend-validated,
  repository-managed daily schedule declaration.
- `src/portfolios/`: reusable Python module for ETF holdings portfolios backed by interpolated
  Alpaca bars.

## Workflow Highlights

- Asset registration is strict. A symbol must exist in Alpaca, resolve to a FIGI, and not already
  exist in Main Sequence by FIGI before it is created.
- ETF extraction is dependency-owned. The external `etfhextractor` package expands holdings and
  plans category membership; Alpaca code in `src/` consumes those outputs through
  `src/etf_holdings.py`.
- Holdings categories are strict. Category sync refuses to run when extracted symbols are missing
  or ambiguous in Main Sequence.
- Bars publishing is reusable. The `AlpacaStockBarsNode` publishes asset-indexed OHLCV tables
  keyed by `frequency_id`, `feed`, and `adjustment`.
- Portfolio construction is reusable. `src/portfolios/` wires `ETFHoldingsSignal` from
  `etfhextractor` into `msm_portfolios.InterpolatedPrices` sourced from Alpaca bars, then into
  `PortfoliosDataNode`.
- The single-asset shorthand and the generic bars runner intentionally differ in defaults. The
  shorthand uses `sip/all`; generic `bars run` defaults to `iex/raw` unless you set flags
  explicitly.

## API Surface

- `GET /health`: basic API health.
- `GET /v1/discovery/config`: supported component providers plus configured ETF seed/provider
  metadata.
- `POST /v1/assets/registration/execute`: execute multi-symbol or ETF-seed-backed asset
  registration.
- `POST /v1/holdings-categories/execute`: execute holdings-category sync after strict validation
  passes.

## Supported ETF Providers

- `ishares`
- `invesco`
- `vanguard`
- `state_street`

## Repository Boundaries

- `src/`: Alpaca-owned registration, bar execution, CLI, jobs, and reusable backend modules.
- `src/etf_holdings.py`: local adapter over `etfhextractor` for ETF seed expansion, provider
  defaults, and holdings-category orchestration.
- `src/portfolios/`: ETF holdings portfolio construction using `msm_portfolios` and Alpaca bars.
- `api/`: thin FastAPI contracts over reusable project services.
- `docs/`: detailed workflow docs and operational notes.
- `tests/`: regression coverage for CLI, API, adapter, and bar support logic.

## Architecture (ms-markets, storage-first)

Market-domain behavior depends on **ms-markets** (`msm`) first, then `mainsequence`. Assets,
categories, and the OHLCV DataNode use the storage-first ms-markets layer:

- `AlpacaStockBarsNode` is an `msm.data_nodes.assets.AssetIndexedDataNode`; its schema lives on a
  storage class in `src/markets_storage/`, indexed by `(time_index, asset_identifier)`.
- Assets are `msm.api.assets.Asset` rows (UUID identity; FIGI as `unique_identifier`); provider
  ticker/figi live on `OpenFigiDetails`.
- Every process attaches the runtime once via `src.runtime.start_markets_engine()`.

Full migration record: `docs/implementation_tasks/0001_ms_markets_storage_first_migration.md`.

## Runtime Prerequisites

- Python 3.13 is required. `pyproject.toml` carries compatible lower bounds while `uv.lock` and
  exported `requirements.txt` capture the resolved environment.
- Live platform actions need an authenticated Main Sequence session.
- The project market tables must be migrated/registered before live writes:
  `mainsequence migrations upgrade --provider src.migrations:migration head`.
- Portfolio runs also require the built-in ms-markets portfolio tables and the dynamic
  `InterpolatedPrices` table for the selected Alpaca bars table UID to be migrated/registered.
- Alpaca credentials are resolved from environment variables first, then from Main Sequence
  secrets.
- Browser-backed ETF extraction flows are handled by `etfhextractor` and may require the
  Playwright browser runtime.

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

- `docs/agent.md`
- `docs/etf_extraction.md`
- `docs/assets/registration.md`
- `docs/assets/holdings_categories.md`
- `docs/data_nodes/alpaca_bars.md`
- `docs/portfolios/etf_holdings_portfolios.md`
- `docs/adrs/0002_etf_holdings_alpaca_portfolio_construction.md`
- `docs/api.md`
- `docs/operations/jobs.md`
- `docs/implementation_tasks/0001_ms_markets_storage_first_migration.md` (ms-markets migration record)
- `docs/implementation_tasks/0002_alpaca_account_module.md` (account storage record)
