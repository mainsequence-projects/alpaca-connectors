# ADR: Calendar-Event Portfolio Timing And Valuation Ownership

## Status

Accepted

## Context

The first portfolio implementation used `ImmediateSignal`, relabeled daily valuation observations
to calendar closes in a connector-owned `PortfoliosDataNode` subclass, and widened the initial
valuation read through another local override. That mixed signal observation time, portfolio
execution time, and valuation time. It could also leave midnight observations in canonical
portfolio history.

`ms-markets` 1.0.13 provides the required temporal architecture: persisted
`PortfolioCalendarEvents`, `CalendarEventSignal`, `PortfolioRebalance`, bounded
`ValuationAlignmentPolicy`, and canonical `PortfolioWeights` and `PortfoliosDataNode` behavior.

## Decision

The connector requires `ms-markets>=1.0.14` and `mainsequence>=8.1.8`, and uses the released
pipeline directly:

```text
Persisted Calendar ─> PortfolioCalendarEvents ─┐
ETF Weight Signal ─────────────────────────────┴> CalendarEventSignal
                                                └> PortfolioRebalance
                                                   └> PortfolioWeights

Alpaca Bars ─> persistent InterpolatedPrices ─────┐
PortfolioWeights ─────────────────────────────────┴> PortfoliosDataNode
```

The Rebalance Configuration owns economic execution timing:

- canonical calendar identifier;
- session label;
- market-open or market-close event;
- signed event offset in seconds;
- every-session or weekly cadence; and
- calendar-local weekday for weekly cadence.

The Portfolio Configuration owns valuation behavior: valuation column, maximum permitted price
staleness, missing-value policy, interpolation settings, and commission fee. The Main Sequence Job
continues to own only operational schedule and compute settings. A Job schedule determines when a
calculation is attempted; it does not define the historical market timestamp at which a rebalance
is recorded.

At each eligible persisted calendar event, `CalendarEventSignal` selects the latest signal
observation at or before that event. The signal timestamp remains the provider observation time and
is not rewritten. The executed weights use the configured calendar event timestamp. Actual stored
session boundaries, including early closes and timezone transitions, are authoritative.

Valuation alignment may select the latest known price at a real portfolio observation only within
the configured maximum staleness. It never creates timestamps or extends the output to the current
time. Persistent `InterpolatedPrices` remains a required input because it defines the valuation
series used by the portfolio; bounded alignment is a separate validity check. Every asset in the
interpolation scope carries the Rebalance Configuration's calendar identifier explicitly. A bare
asset identifier would default to a `24/7` calendar in ms-markets and would incorrectly materialize
daily observations at 23:59 UTC instead of the configured exchange session event.

Existing connector Rebalance Configuration rows are migrated to NYSE regular-session market close,
zero offset, every session. The local timestamp-remapping and initial-lookback subclasses are
deleted. Existing legacy midnight portfolio observations are historical data and require the
released ms-markets timestamp-repair workflow before they should be treated as canonical history.

The first integration against 1.0.7 exposed an upstream `timedelta` serialization defect during
PortfolioWeights hashing. [MainSequenceMarkets issue
5](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/5) was fixed in 1.0.8 by
requiring Main Sequence SDK 8.1.7, which canonically serializes duration-bearing updater
configuration. Version 1.0.10 retained that runtime fix and repaired set-based seed reads for
multi-dimensional signal storage. Version 1.0.11 normalizes time-index and timestamp columns to
nanosecond UTC before publication, preventing values such as `open_time` from being serialized
with the wrong datetime unit. Version 1.0.12 scopes strict valuation coverage to assets that are
actually held or cross a real entry/exit boundary: a zero-to-zero signal constituent no longer
requires a fresh price, while entries and liquidations still do. Version 1.0.13 makes
`asset_identifier` the canonical interpolation-scope key and removes out-of-range seed weights
before enforcing unique `(time_index, asset_identifier)` coordinates. The connector uses Main
Sequence SDK 8.1.8 and upgrades both lower bounds together before repairing and replaying legacy
rows.

## Consequences

- signal observation, rebalance execution, and valuation timestamps have separate owners;
- a signal observed after the configured close becomes eligible at the next configured event;
- early closes are represented by their actual persisted close times;
- Job crontab timezone cannot change portfolio economic timestamps;
- no connector code monkeypatches or subclasses canonical ms-markets portfolio valuation logic;
- `PortfolioRebalanceStateStorage`, `PortfolioWeightsStorage`, and `PortfoliosStorage` are distinct
  stages that can be inspected independently; and
- `ImmediateSignal` is no longer exposed by this connector workflow.

## Acceptance Criteria

- Rebalance Configuration CRUD round-trips every calendar-event field.
- Portfolio Configuration CRUD round-trips bounded valuation-alignment fields and stores no Job
  schedule values.
- Execution explicitly updates interpolation, calendar events, rebalance state, weights, and
  portfolio valuation without rerunning signal extraction or raw bars.
- New weights use persisted market-event timestamps and new portfolio values use valuation-source
  timestamps; neither uses midnight normalization.
- The project migration upgrades existing configuration rows without losing their Job or Portfolio
  relationships.
