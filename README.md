# alpaca-connectors

## Quickstart

```bash
uv sync
```

## Documentation

```bash
uv run mkdocs serve
```

The project documentation lives under `docs/` and is configured by `mkdocs.yml`.

Architecture split:

- `src/` owns Alpaca registration, Alpaca bars execution, and CLI/runtime entrypoints
- `etf_extraction/` owns ETF provider extraction, ETF settings, ETF holdings category logic, and ETF-oriented tests

Key pages:

- `docs/etf_extraction.md`
- `docs/assets/registration.md`
- `docs/assets/holdings_categories.md`
- `docs/data_nodes/alpaca_bars.md`
- `docs/command_center/app_component.md`

Project CLI:

- `alpaca-connectors asset register`
- `alpaca-connectors holdings-category create`
- `alpaca-connectors bars run`
- `alpaca-connectors asset <ticker> update_prices <period>`
