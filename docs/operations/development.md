# Development Workflow

## Install

```bash
uv sync
```

## Docs

```bash
uv run mkdocs serve
```

## Browser Runtime

Some ETF provider extraction paths need browser support.

```bash
bash scripts/install_browser_runtime.sh
```

## Common Commands

### Register assets

```bash
.venv/bin/python scripts/register_asset.py --symbols NVDA,AAPL
```

### Register from ETF holdings

```bash
.venv/bin/python scripts/register_asset.py --seed-tickers IVV --component-provider ishares --execute
```

### Create holdings category

```bash
.venv/bin/python scripts/create_holdings_category.py --etf-ticker IVV --execute
```

### Run daily bars for a category

```bash
.venv/bin/python scripts/run_daily_stock_bars.py \
  --asset-category-unique-identifier HOLDINGS__IVV \
  --frequency-id 1d \
  --feed sip \
  --adjustment all
```

### Run daily bars for one ticker

```bash
.venv/bin/python scripts/run_daily_stock_bars.py \
  --tickers NVDA \
  --frequency-id 1d \
  --feed sip \
  --adjustment all
```

## VS Code Launch Configurations

The repo includes launch configurations for:

- asset registration from `IVV`
- holdings-category creation for `IVV`
- daily bars for `HOLDINGS__IVV`
- daily bars for `NVDA`
