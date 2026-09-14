---
name: alpaca-etf-portfolios
description: Configure calendar-event rebalancing and build analytical ETF portfolios from stored ETF weight signals and migrated Alpaca bars.
---

# Alpaca ETF Portfolios

Use this skill for rebalance-configuration CRUD and analytical ETF portfolio configuration, runs,
history, and statistics. In a CodeRepository Executor session, use
`alpaca_query_rebalance_configurations`, `alpaca_manage_rebalance_configuration`,
`alpaca_query_etf_portfolios`, and `alpaca_manage_etf_portfolio`. In a shell, use the installed
`alpaca-connectors` command.

## Portfolio Contract

- A portfolio configuration references one existing ETF signal configuration, one existing bars
  configuration, and one stored rebalance configuration.
- The rebalance configuration owns market calendar, session, market-open/close event, event offset,
  and cadence. The platform Job owns schedule and compute settings; do not duplicate those fields in
  the portfolio table.
- Phase 1 uses `calendar_event_signal`, daily persistent `InterpolatedPrices`, forward-fill
  interpolation, and actual calendar event timestamps. It must not create midnight timestamps.
- Price interpolation can fill between real price observations but does not make missing source
  coverage unlimited. Apply the configured maximum staleness and `fail_on_missing_prices` policy.
- The portfolio is analytical. This agent does not place trades or manage a brokerage portfolio.

## Workflow

1. Query or create a rebalance configuration using the rebalance tools.
2. Query the selected signal and bar configurations and verify their relationships and data scope.
3. Create one ETF portfolio configuration with its dedicated Job settings.
4. Submit operation `run`, poll `alpaca_get_job_run_status`, and inspect the resulting detail with
   `alpaca_query_etf_portfolios` operation `get`.
5. Report linked configuration details, historical value observations, and the existing
   `empyrical-reloaded` statistics. Do not recalculate them with an unrelated method.

Shell equivalents:

```shell
alpaca-connectors portfolio rebalance list
alpaca-connectors portfolio list
alpaca-connectors portfolio get <CONFIGURATION_UID>
alpaca-connectors portfolio run <CONFIGURATION_UID>
alpaca-connectors portfolio runs <CONFIGURATION_UID>
```

Never invent a Bars dataset or Signal UID; resolve them through the stored configuration foreign
keys. Tau deletion requires exact `DELETE <configuration_uid>` confirmation. Deleting a
configuration retains already published portfolio data.
