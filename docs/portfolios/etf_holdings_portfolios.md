# ETF Holdings Portfolios

## Goal

Build ms-markets portfolios that track ETF holdings while using this project's Alpaca bars as the
valuation source.

Main module:

- `src/portfolios/etf_tracking.py`

Reference ADR:

- `docs/adrs/0002_etf_holdings_alpaca_portfolio_construction.md`

## Dependency Stack

- `etfhextractor`: ETF holdings extraction, `ETFHoldingsSignal`, calendar persistence, and reusable
  portfolio-graph assembly
- `msm_portfolios`: interpolated prices, signal storage, portfolio values, portfolio weights, and
  portfolio identity
- `src/market_data/storage.py`: project-owned Alpaca source bars

## Current Python API

Plan without writing portfolio rows:

```python
from src.portfolios import (
    AlpacaEtfTrackingPortfolioConfig,
    plan_alpaca_etf_tracking_portfolio,
)

plan = plan_alpaca_etf_tracking_portfolio(
    AlpacaEtfTrackingPortfolioConfig(
        etf_ticker="IVV",
        provider="ishares",
        frequency_id="1d",
        feed="sip",
        adjustment="all",
        valuation_column="close",
        allowed_asset_classes=("Equity",),
    )
)
print(plan.summary())
```

Build the portfolio graph:

```python
from src.portfolios import (
    AlpacaEtfTrackingPortfolioConfig,
    build_alpaca_etf_tracking_portfolio,
)

build = build_alpaca_etf_tracking_portfolio(
    AlpacaEtfTrackingPortfolioConfig(etf_ticker="IVV", provider="ishares"),
    run=False,
)
print(build.summary())
```

Run the DataNode graph only after the required tables are migrated and the Alpaca bars table has
data:

```python
build = build_alpaca_etf_tracking_portfolio(
    AlpacaEtfTrackingPortfolioConfig(etf_ticker="IVV", provider="ishares"),
    run=True,
)
```

## Required Platform State

Before running the portfolio graph:

- ETF component assets must be registered as ms-markets assets with FIGI as
  `Asset.unique_identifier`
- `OpenFigiDetails` / snapshots must contain ticker facts for component resolution
- selected Alpaca bars storage must be migrated and registered
- selected Alpaca bars DataNode must have bars for the component universe
- dynamic `InterpolatedPrices` storage for the selected source table UID must be migrated and
  registered
- ms-markets portfolio built-in tables must be migrated and registered

## Source Bars

The default source is:

```text
frequency_id = 1d
feed         = sip
adjustment   = all
```

This resolves to:

```text
alpaca_stock_bars_1d_sip_all
```

The module can also use:

```text
alpaca_stock_bars_1d_iex_raw
```

or any future registered `(frequency_id, feed, adjustment)` triple added to
`src/market_data/storage.py`.

## Interpolated Prices

The portfolio does not consume raw Alpaca bars directly. It creates an `InterpolatedPrices` node
from `msm_portfolios` and passes that node into the current portfolio build contract as:

```text
PortfolioBuildConfiguration(
    valuation_source_instance=<InterpolatedPrices>,
    valuation_column="close",
)
```

The interpolation node is configured with:

- source Alpaca bars `TimeIndexMetaTable.uid`
- explicit ETF component asset list
- `upsample_frequency_id`
- `intraday_bar_interpolation_rule`
- `valuation_column`, defaulting to `close` for Alpaca OHLCV bars

For daily Alpaca bars, the default is:

```text
upsample_frequency_id = 1d
intraday_bar_interpolation_rule = ffill
valuation_column = close
```

The dynamic storage identity includes the source table UID, so the interpolation table must be
prepared for each registered source table.

After constructing that Alpaca-specific valuation source, the project delegates calendar, signal,
Portfolio row, configuration, and `PortfoliosDataNode` assembly to
`etfhextractor.build_etf_tracking_portfolio(...)`. It passes the explicit configuration-derived
`...__ALPACA` portfolio identifier, so delegation does not change portfolio identity.

## Holdings Filter

The upgraded `etfhextractor` signal config carries the holdings asset-class filter. This project
uses the same value for both preflight resolution and the `ETFHoldingsSignalConfig` so the planned
component universe matches the signal universe.

The default is:

```text
allowed_asset_classes = ("Equity",)
```

Set it to `None` only when a portfolio intentionally tracks every provider holding class.

## Portfolio Identity

The default `Portfolio.unique_identifier` includes the ETF, bars configuration, interpolation
configuration, and the Alpaca venue suffix at the end. The only double-underscore segment is
`__ALPACA`.

```text
<ETF>_TRACKER_BARS_<FREQ>_<FEED>_<ADJUSTMENT>_INTERP_<UPSAMPLE>_<RULE>__ALPACA
```

Example:

```text
IVV_TRACKER_BARS_1D_SIP_ALL_INTERP_1D_FFILL__ALPACA
```

Do not use a ticker-only identity such as `etf_tracker_<ETF>` for this project. Two portfolios that
use the same ETF but different Alpaca price or interpolation configuration are different portfolio
rows.

## Runtime

Use the portfolio runtime path:

```python
from src.runtime import start_portfolio_markets_engine

start_portfolio_markets_engine()
```

Do not start the lighter asset/category runtime first in the same process if the process will build
or run portfolios. A process may only attach one compatible ms-markets runtime model set.

## Live Verification

After a live run, verify:

- signal weights exist in `SignalWeightsStorage`
- interpolated prices exist in the configured dynamic `InterpolatedPrices` storage
- portfolio values exist in `PortfoliosStorage`
- portfolio weights exist in `PortfolioWeightsStorage`
- `PortfolioTable.unique_identifier` exists for the configuration-specific identifier, for example
  `IVV_TRACKER_BARS_1D_SIP_ALL_INTERP_1D_FFILL__ALPACA`

These checks require an authenticated platform session and migrated tables.
