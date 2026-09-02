# ADR: ETF Holdings Portfolios With Alpaca Interpolated Prices

## Status

Accepted

## Context

The project already owns Alpaca-backed public-equity asset registration, ETF holdings-category
orchestration through `etfhextractor`, and Alpaca OHLCV bars in project-owned ms-markets storage
tables.

The next extension is portfolio construction:

- use ETF holdings weights as the signal
- use this project's Alpaca bars as the valuation source
- construct an interpolated portfolio valuation source with `msm_portfolios`
- publish portfolio values and portfolio weights through the ms-markets portfolio graph

The external `etfhextractor` project already demonstrates the generic pattern in
`examples/ivv_tracking_portfolio_full_workflow.py`: ETF holdings become a custom
`ETFHoldingsSignal`, and `PortfoliosDataNode` consumes that signal plus a valuation source.

This repository must adapt that pattern to its own real Alpaca bars tables instead of the
example's deterministic demo bars table.

The current `msm_portfolios` build contract is valuation-based:

```text
PortfolioBuildConfiguration
  valuation_source_instance = InterpolatedPrices(...)
  valuation_column          = close
```

The current `etfhextractor` signal contract also carries the holdings asset-class filter:

```text
ETFHoldingsSignalConfig
  allowed_asset_classes = ("Equity",)
```

This project keeps that filter aligned between the preflight resolver and the signal node.

## Decision

Add a local portfolio extension module:

```text
src/portfolios/
```

The module builds ETF-tracking portfolios with this dependency direction:

```text
+---------------------+       +---------------------------+
| etfhextractor       |       | Alpaca bars DataNode      |
| ETF holdings reader |       | alpaca_stock_bars_*      |
+----------+----------+       +-------------+-------------+
           |                                |
           | component weights              | OHLCV bars
           v                                v
+---------------------+       +---------------------------+
| ETFHoldingsSignal   |       | InterpolatedPrices        |
| SignalWeights       |       | dynamic price storage     |
+----------+----------+       +-------------+-------------+
           |                                |
           | signal_weight                  | close / volume
           +----------------+---------------+
                            |
                            v
                  +-------------------+
                  | PortfoliosDataNode|
                  | values + weights  |
                  +---------+---------+
                            |
                            v
                  +-----------------------------------------------+
                  | Portfolio row                                 |
                  | <ETF>_TRACKER_BARS_<cfg>_INTERP_<c>__ALPACA   |
                  +-----------------------------------------------+
```

The runtime workflow is:

```text
1. Register assets by FIGI
2. Publish Alpaca bars for the ETF component universe
3. Migrate/register the Alpaca bars storage table
4. Prepare the dynamic InterpolatedPrices storage for the selected Alpaca bars table UID
5. Extract ETF holdings through etfhextractor
6. Resolve component tickers through ms-markets snapshots/details to Asset.unique_identifier
7. Build ETFHoldingsSignal
8. Build InterpolatedPrices from Alpaca bars
9. Build PortfoliosDataNode
10. Run the portfolio graph
```

## Implementation Boundary

`src/portfolios/etf_tracking.py` owns:

- resolving the registered Alpaca bars `TimeIndexMetaTable.uid`
- resolving ETF component tickers to `Asset.unique_identifier`
- constructing `InterpolatedPrices` from the Alpaca bars table
- passing the Alpaca-specific price source, valuation column, universe, presentation metadata,
  and configuration-specific portfolio identity to the reusable `etfhextractor` portfolio builder
- returning plan/build summaries for jobs, CLI, or API layers
- building `Portfolio.unique_identifier` from ETF + bars configuration + interpolation
  configuration + Alpaca venue suffix, for example
  `IVV_TRACKER_BARS_1D_SIP_ALL_INTERP_1D_FFILL__ALPACA`

`etfhextractor` owns:

- provider holdings extraction
- ETF holdings signal implementation
- `ETFHoldingsSignalConfig.allowed_asset_classes`
- component weight derivation
- ticker-to-asset identifier resolution helpers
- trading-calendar persistence
- reusable `ETFHoldingsSignal -> valuation source -> PortfoliosDataNode` graph construction
- the CLI-facing `publish_etf_tracking_portfolio` summary wrapper

The Alpaca workflow calls `etfhextractor.build_etf_tracking_portfolio` directly. The migrated
builder accepts an explicit `portfolio_unique_identifier`, so the project keeps the
configuration-specific identity ending in `__ALPACA` while the dependency remains the single owner
of signal, calendar, portfolio-row, configuration, and `PortfoliosDataNode` assembly.

`msm_portfolios` owns:

- `SignalWeightsStorage`
- `InterpolatedPricesStorage`
- `PortfoliosStorage`
- `PortfolioWeightsStorage`
- `PortfolioTable` identity
- rebalance strategy semantics

`src/data_nodes/alpaca_bars.py` and `src/markets_storage/alpaca_bars.py` still own:

- fetching Alpaca bars
- storage identity per `(frequency_id, feed, adjustment)`
- Alpaca bars schema and cadence

## Storage And Migration Rule

The source Alpaca bars table is project-owned and static:

```text
alpaca_stock_bars_1d_sip_all
alpaca_stock_bars_1d_iex_raw
```

The interpolated price table is `msm_portfolios`-owned and dynamic. Its storage identity includes:

- source Alpaca bars `TimeIndexMetaTable.uid`
- source cadence
- upsample frequency
- interpolation rule

Therefore normal portfolio runtime code must not silently create this schema. A preparation step
must derive the dynamic `InterpolatedPrices` storage class and migrate/register it before live
portfolio execution.

## Required CLI Surface

This extension should expose a first-class CLI command for operators. The command should build the
portfolio graph from explicit configuration and should support planning separately from execution.

Proposed command shape:

```bash
alpaca-connectors portfolio build-etf \
  --etf-ticker IVV \
  --provider ishares \
  --frequency-id 1d \
  --feed sip \
  --adjustment all \
  --upsample-frequency-id 1d \
  --interpolation-rule ffill \
  --valuation-column close \
  --calendar-key NYSE \
  --backtest-start-days 60 \
  --signal-validity-days 90 \
  --allowed-asset-class Equity \
  --commission-fee 0.00018 \
  --plan-only
```

Execution should use the same command with an explicit write/run flag:

```bash
alpaca-connectors portfolio build-etf \
  --etf-ticker IVV \
  --provider ishares \
  --frequency-id 1d \
  --feed sip \
  --adjustment all \
  --upsample-frequency-id 1d \
  --interpolation-rule ffill \
  --valuation-column close \
  --calendar-key NYSE \
  --allowed-asset-class Equity \
  --execute
```

The CLI must also extend/pass through the relevant `etfhextractor` mapping arguments used to
resolve provider holdings symbols into registered ms-markets assets. At minimum it should support:

- `--fund-url` as an alternative to `--provider`
- repeatable `--figi-filter JSON` entries, matching the `etfhextractor` convention for
  ticker-to-FIGI disambiguation and provider ticker aliasing
- a mapping/alias argument for provider tickers that differ from OpenFIGI/Alpaca conventions, for
  example share-class mappings such as provider `BRKB` to FIGI ticker `BRK/B`

Example with mapping arguments:

```bash
alpaca-connectors portfolio build-etf \
  --etf-ticker IVV \
  --provider ishares \
  --frequency-id 1d \
  --feed sip \
  --adjustment all \
  --upsample-frequency-id 1d \
  --interpolation-rule ffill \
  --valuation-column close \
  --allowed-asset-class Equity \
  --figi-filter '{"ticker":"BRKB","figi_ticker":"BRK/B"}' \
  --figi-filter '{"ticker":"BFB","figi_ticker":"BF/B"}' \
  --execute
```

The CLI must fail loudly when mappings still leave missing or ambiguous components. It must not
silently drop ETF holdings from the signal or construct a portfolio from a partial universe.

## Documentation Requirement

This portfolio extension must stay documented in:

- this ADR
- `docs/portfolios/etf_holdings_portfolios.md`
- `README.md`
- `mkdocs.yml`

Any future CLI command, scheduled job, or API route that runs this portfolio flow must document:

- required asset registration state
- required Alpaca bars table and cadence
- required dynamic interpolation-storage migration
- whether the command only plans, writes metadata, or runs DataNodes
- exact live verification steps for signal rows, interpolated prices, portfolio values, portfolio
  weights, and the `Portfolio` row

## Consequences

Positive:

- portfolio construction reuses the existing Alpaca bars investment in this repository
- ETF holdings logic remains delegated to `etfhextractor`
- price interpolation uses the supported `msm_portfolios` price node instead of custom ad hoc
  forward-fill code
- portfolio identity stays in `PortfolioTable`, not `AssetTable`

Tradeoffs:

- dynamic interpolation storage needs an explicit preparation/migration workflow
- portfolio execution requires the portfolio-capable runtime graph, not the lighter asset/category
  runtime graph
- one ETF can have multiple valid portfolio rows when the Alpaca price source or interpolation
  configuration changes; those rows must not collapse to one `etf_tracker_<ETF>` identity
- missing ETF components remain blockers until assets are registered by FIGI and bars exist for the
  selected Alpaca bars table

## Notes

The initial implementation is a reusable Python module, not a CLI command. CLI/API/job surfaces can
be added after the dynamic interpolation-storage preparation workflow is finalized and documented.
