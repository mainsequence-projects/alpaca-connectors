---
name: alpaca-portfolios
description: Read, run, pause, resume and delete analytical portfolios built from the weights an application provides, manage their calendar-event rebalance rules, and inspect runs, performance and statistics through Alpaca MCP tools.
---

# Analytical Portfolios

Discover schemas with `alpaca__list_tools`; use `alpaca__call_tool` with `tool` and `arguments`.
Direct MCP clients use inner names and arguments with `tools/call`. The API verifies identity.
Call `alpaca_project_state` with empty arguments for readiness when needed.
No shell, SQL, trade submission or brokerage-portfolio management is supported.

## What A Portfolio Is

Every portfolio is built from the weights an application provides. That application creates it,
gives it new weights and owns it; you never create a portfolio or give it weights. If asked to,
say so plainly and suggest asking the application that owns the portfolio.

- Each observation of its weights is the whole set at its time; an asset left out is held at zero.
- It rebalances to the latest observation at its rebalance rule's next calendar event and is
  valued with the daily Alpaca prices of its bars configuration.
- Pausing, resuming and deleting are done only for its owner. Another owner's portfolio answers
  `portfolio_owned_by_another`; report it and stop.

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

## Read

`alpaca_query_portfolios` supports `list`, `get` and `runs`. `get` returns the linked bars and
rebalance details, the provided weights' signal with its first and last observation and asset
count, the history of values and existing `empyrical-reloaded` statistics. Do not calculate
inconsistent statistics or mistake historical success for the latest run's status.

```json
{"tool":"alpaca_query_portfolios","arguments":{"operation":"get","configuration_uid":"CONFIGURATION_UID","observation_limit":2500}}
```

Price coverage follows the portfolio's settings:

- Forward-fill reuses known prices; it cannot invent a first observation or make stale coverage
  unlimited. Maximum staleness still applies to required exposure.
- `fail_on_missing_prices: true` fails on unusable required valuations. Do not describe a failed
  valuation as a gap the portfolio tolerates.
- `commission_fee` is a fraction: `0.00018` = `0.018%` = `1.8` basis points, not `1.8%`.

## Run, Pause, Resume

`alpaca_manage_portfolio` supports `run`, `pause`, `resume` and `delete`. The shared workflow Job
owns UTC scheduling and compute; pausing leaves the portfolio out of that Job's daily scheduled run
and resuming puts it back. Neither defines the portfolio's market rebalance time.

A run needs the shared Job and an allowed preflight: enabled linked configurations and published
weights. Submit an authorized `run`, then inspect `alpaca_get_job_run_status` with the returned
UID. Do not claim success from submission or automatically resubmit a failed run.

```json
{"tool":"alpaca_manage_portfolio","arguments":{"operation":"run","configuration_uid":"CONFIGURATION_UID"}}
```

```json
{"tool":"alpaca_manage_portfolio","arguments":{"operation":"pause","configuration_uid":"CONFIGURATION_UID"}}
```

## Delete

Deletion requires the person's exact `DELETE <uid>` confirmation. It retires the portfolio, then
deletes its configuration; it is refused with `portfolio_in_account_target` while an account's
target names the portfolio, so that account's owner changes the target first. Retiring keeps the
portfolio's series and history.

Portfolio/rebalance list limits are 1–100 and offsets are multiples of the fixed limit.
Detail observation limits are 1–5000; run-history limits are 1–100.
