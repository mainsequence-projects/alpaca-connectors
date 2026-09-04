# ETF Holdings Portfolios

## Goal

Build an ms-markets portfolio from one registered Asset Universe's observed ETF holdings and this
project's Alpaca bars.

The implementation lives in:

- `src/portfolios/alpaca_etf_signal.py`
- `src/portfolios/etf_tracking.py`

See [ADR 0006](../adrs/0006_universe_backed_alpaca_etf_signal.md) for signal identity and
observation semantics.

## Ownership

- `etfhextractor` extracts provider holdings and derives component weights.
- `AlpacaETFHoldingsSignal` owns the connector-specific execution: registered Universe lookup,
  account Secret-name resolution, bulk Alpaca registration, category materialization, and signal
  frame production.
- `msm_portfolios` owns `SignalWeightsStorage`, interpolated prices, Portfolio identity, portfolio
  values, and portfolio weights.
- `src/market_data/storage.py` owns the source Alpaca bar tables.

The signal defines no custom table. Its configuration is exactly:

```python
AlpacaETFHoldingsSignalConfig(
    universe_uid="<UNIVERSE_UID>",
    account_uid="<ACCOUNT_UID>",
)
```

`universe_uid` defines the stable signal UID. `account_uid` remains part of the serialized updater
configuration so automatic execution can resolve the correct registered account, but it is
excluded from `_signal_uid_payload()` and never appears in `SignalWeightsStorage`.

## Build API

Plan the graph without writing assets, signals, calendars, or portfolios:

```python
from src.portfolios import (
    AlpacaEtfTrackingPortfolioConfig,
    plan_alpaca_etf_tracking_portfolio,
)

config = AlpacaEtfTrackingPortfolioConfig(
    universe_uid="<UNIVERSE_UID>",
    account_uid="<ACCOUNT_UID>",
    frequency_id="1d",
    feed="sip",
    adjustment="all",
)
plan = plan_alpaca_etf_tracking_portfolio(config)
print(plan.summary())
```

Build or execute the graph:

```python
from src.portfolios import build_alpaca_etf_tracking_portfolio

build = build_alpaca_etf_tracking_portfolio(config, run=False)
result = build_alpaca_etf_tracking_portfolio(config, run=True)
```

Planning extracts once and prepares a transient execution plan. When the same process executes the
graph, the signal consumes that plan rather than extracting again. The plan is not serialized and
does not affect updater or signal identity.

## Signal Observation Semantics

Each successful update writes one complete batch with the canonical grain:

```text
(time_index, signal_uid, asset_identifier) -> signal_weight
```

Provider percentage weights are normalized to sum to one. An unchanged extraction is still a new
observation. The signal does not backdate the first observation, suppress unchanged weights, or
rewrite timestamps to a market-session close.

`time_index` records when this application observed the extracted provider response. It does not
guarantee that the provider weights became economically effective at precisely that time. This is
an observed-snapshot series, not perfect point-in-time holdings history.

## Required Platform State

Before execution:

- the Asset Universe, its Universe Source, linked Asset Category, and registered Alpaca Account
  must exist
- the account's referenced Main Sequence Secrets must be readable at runtime
- built-in ms-markets signal and portfolio tables must be migrated and registered
- the selected Alpaca bars table and dynamic `InterpolatedPrices` table must be migrated
- the bars table must contain prices for the current component assets

Universe execution registers missing Alpaca-backed component Assets automatically. It does so in
bulk and replaces category memberships in bulk; it does not perform a backend request per asset.

## Runtime And Verification

Use `start_portfolio_markets_engine()` for the complete portfolio graph. Normal API and Universe
execution include `SignalMetadataTable` and `SignalWeightsStorage` in their application runtime.

After a live run, verify:

- the linked Asset Category contains the extracted components
- `SignalWeightsStorage` contains one observation under the Universe-derived signal UID
- interpolated prices exist for those asset identifiers
- `PortfoliosStorage` and `PortfolioWeightsStorage` contain the resulting portfolio output
