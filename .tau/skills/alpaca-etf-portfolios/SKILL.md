---
name: alpaca-etf-portfolios
description: Manage calendar-event rebalancing and the existing analytical portfolios built from stored signals and bars, then run, inspect, pause or end them through Alpaca MCP tools.
---

# Analytical ETF Portfolios

Discover schemas with `alpaca__list_tools`; use `alpaca__call_tool` with `tool` and `arguments`.
Direct MCP clients use inner names and arguments with `tools/call`. The API verifies identity.
Call `alpaca_project_state` with empty arguments for readiness when needed.
No shell, SQL, trade submission or brokerage-portfolio management is supported.

## Rebalance Policy

Use `alpaca_query_rebalance_configurations` operations `list`/`get` and
`alpaca_manage_rebalance_configuration` operations `create`/`update`/`delete`.
The supported strategy is `calendar_event_signal`. It owns market calendar, session,
open/close event, offset and cadence. Do not substitute midnight timestamps or a UTC Job cron
for actual market-calendar events.

Offsets are seconds relative to the event: negative before, positive after. Weekly cadence uses
weekday 0 (Monday) through 6 (Sunday); every-session cadence follows trading sessions.
Use the person's requested calendar/session. Example arguments to `alpaca__call_tool` for
a requested NYSE regular-session closing rebalance:

```json
{"tool":"alpaca_manage_rebalance_configuration","arguments":{"operation":"create","request":{"name":"NYSE session close","strategy":"calendar_event_signal","calendar_identifier":"NYSE","session_label":"regular","rebalance_event":"market_close","event_offset_seconds":0,"rebalance_cadence":"every_session","rebalance_weekday":0}}}
```

## Portfolio Configuration

No ETF portfolio is created any more, and you create no portfolio: an application creates a
portfolio from the weights it provides, and the existing ETF portfolios run until they are
deleted. If asked to build one, say so plainly.
`alpaca_manage_etf_portfolio` supports `update`, `run`, `delete`. A portfolio built from provided
weights is changed or deleted only for its owner.
The shared workflow Job owns UTC scheduling and compute; `scheduled` only controls inclusion.
It does not define the portfolio's market rebalance time.

Phase 1 uses persistent daily `InterpolatedPrices`, `ffill` and actual calendar events.
Never invent dataset/Signal UIDs, synthesize midnight prices or silently weaken price policies.

- Forward-fill reuses known prices; it cannot invent a first observation or make stale coverage
  unlimited. Maximum staleness still applies to required exposure.
- `fail_on_missing_prices: true` fails on unusable required valuations. False is best-effort
  processing, not a guarantee of valid output. Do not toggle it to conceal a failure.
- `commission_fee` is a fraction: `0.00018` = `0.018%` = `1.8` basis points, not `1.8%`.
  Convert the person's percentage or basis points before submitting.
- Use validated phase-1 values and the requested staleness policy. Pausing is an `update` with
  `scheduled: false`; replace the UID placeholder with a returned identifier.

```json
{"tool":"alpaca_manage_etf_portfolio","arguments":{"operation":"update","configuration_uid":"CONFIGURATION_UID","request":{"scheduled":false}}}
```

## Submit And Verify

Inspect `alpaca_query_etf_portfolios` operation `get` for existing configurations. A run needs
the shared Job and an allowed preflight, including enabled linked configurations and published
signal observations. Submit authorized `run`, then inspect `alpaca_get_job_run_status` with the
returned UID. Do not claim success from submission or automatically resubmit a failed run.

```json
{"tool":"alpaca_manage_etf_portfolio","arguments":{"operation":"run","configuration_uid":"CONFIGURATION_UID"}}
```

After success, query `get` for details, historical performance and existing
`empyrical-reloaded` statistics; use `runs` for history. Do not calculate inconsistent stats
or mistake historical success for the latest run's status.

```json
{"tool":"alpaca_query_etf_portfolios","arguments":{"operation":"get","configuration_uid":"CONFIGURATION_UID","observation_limit":2500}}
```

Portfolio/rebalance list limits are 1–100 and offsets are multiples of the fixed limit.
Detail observation limits are 1–5000; run-history limits are 1–100.
Deletion requires the person's exact `DELETE <uid>` confirmation. It retires the portfolio, then
deletes its configuration; it is refused while an account's target names the portfolio, so that
account's owner changes the target first. Retiring keeps the portfolio's series and history.
