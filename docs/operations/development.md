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
alpaca-connectors asset register --symbols NVDA,AAPL
```

### Register from ETF holdings

```bash
alpaca-connectors asset register --seed-tickers IVV --component-provider ishares --execute
```

### Create holdings category

```bash
alpaca-connectors holdings-category create --etf-ticker IVV --execute
```

This creates or refreshes the ETF-owned holdings category. The fixed Alpaca bars job that
targets `HOLDINGS__IVV` assumes that category already exists before it runs.

### Run daily bars for a category

```bash
alpaca-connectors bars run \
  --asset-category-unique-identifier HOLDINGS__IVV \
  --frequency-id 1d \
  --feed sip \
  --adjustment all
```

### Run daily bars for one ticker

```bash
alpaca-connectors bars run \
  --tickers NVDA \
  --frequency-id 1d \
  --feed sip \
  --adjustment all
```

### Shorthand asset price update

```bash
alpaca-connectors asset IVV update_prices daily
```

## VS Code Launch Configurations

The repo includes launch configurations for:

- asset registration from `IVV`
- holdings-category creation for `IVV`
- daily bars for `HOLDINGS__IVV`
- daily bars for `NVDA`
