# ADR: Portfolio Configuration, Immediate-Signal Backtests, And Job Ownership

## Status

Accepted

## Context

The project needs a durable definition for an analytical ETF portfolio assembled from an existing
ETF weight signal, Alpaca bars, persistent price interpolation, and a rebalance policy. The current
prototype in `src/portfolios/etf_tracking.py` constructs this configuration transiently.

A portfolio definition and a Main Sequence Job have different responsibilities:

- the portfolio definition describes what is calculated;
- the Job describes when and with what runtime resources the calculation executes; and
- each JobRun records one execution.

Copying schedule or compute fields into a project MetaTable would create two writable sources of
truth because those fields already belong to the Main Sequence Job. The existing
`AlpacaETFSignalJobConfiguration` design made that mistake by persisting desired schedule and
compute state and projecting it into a Job. That ownership pattern must not be copied into the
portfolio implementation and should be removed from the signal implementation in a follow-up
migration.

The current portfolio prototype also instantiates exactly one rebalance strategy:
`ImmediateSignal`. In the installed `ms-markets` 1.0.3 implementation, `ImmediateSignal` turns each
signal observation directly into current portfolio weights and uses the previous observation as
the before-rebalance weights. Other strategy classes exist in the package, but the currently
available `TimeWeighted` and `VolumeParticipation` implementations raise `NotImplementedError`.

## Decision

### Configuration ownership

One durable Portfolio Configuration row represents one analytical portfolio definition. It owns
only business and calculation inputs:

- a reference to one durable ETF weight Signal Configuration;
- a reference to one Alpaca Bars Configuration;
- a reference to one Rebalance Configuration;
- the interpolation frequency and interpolation rule used to derive `InterpolatedPrices`;
- the valuation column and price-alignment policy;
- the commission-fee assumption and other supported portfolio-calculation parameters; and
- the resulting `PortfolioTable` identity once materialized.

The Portfolio Configuration must not copy:

- interval or crontab schedule fields;
- CPU, memory, GPU, spot, or maximum-runtime fields;
- automatic-deployment or image state;
- Job lifecycle state or JobRun status;
- Universe UID or Alpaca Account UID already owned by the selected Signal Configuration;
- raw bars `TimeIndexMetaTable` UID already resolved by the selected Bars Configuration; or
- signal weights, interpolated prices, portfolio weights, or portfolio values.

A Portfolio Configuration may store the unique UID of its dedicated Job because this is the
relationship used to resolve which portfolio definition a JobRun executes. The relationship is not
a copy of the Job's operational configuration.

The Main Sequence Job is the sole source of truth for:

- execution path;
- interval or crontab schedule;
- CPU, memory, GPU, spot, and maximum runtime;
- automatic deployment and image association; and
- whether a schedule is currently attached.

The Main Sequence JobRun is the sole source of truth for one execution's status, timestamps,
resolved commit and image, logs, and failure details.

The API and GUI may create a Portfolio Configuration and its dedicated Job in one workflow, but
each attribute is written to its owning resource only. Portfolio detail responses compose the
Portfolio Configuration with a live Job read when operational information is requested. Updating
a schedule or compute request patches the Job directly and does not update the Portfolio
Configuration.

### Phase-1 rebalance and backtest scope

Phase 1 supports exactly one Rebalance Configuration behavior: `ImmediateSignal`. The GUI and CLI
must not offer `TimeWeighted`, `VolumeParticipation`, or another unimplemented strategy.

For every available signal observation, the analytical backtest assumes the portfolio immediately
adopts those signal weights at that observation timestamp. This is an analytical assumption, not a
claim of live execution. It models no execution latency, partial fills, volume participation,
market impact, or slippage beyond the configured commission-fee assumption.

The ETF weight signal records when the application observed the provider's holdings. It does not
guarantee that the weights became economically effective at that exact timestamp. Consequently,
an Immediate-Signal portfolio is an observation-time reconstruction and must not be described as
perfect point-in-time ETF replication.

`ms-markets` is not being declared universally limited to `ImmediateSignal`. This ADR limits only
the currently supported Alpaca connector portfolio workflow. A later strategy becomes supported
only after it has an executable implementation, durable typed configuration, tests, API/CLI
validation, and documented backtest semantics.

### Resolution and execution graph

The runtime resolves references instead of copying their data:

```text
Signal Configuration ───────────────> ETF weight Signal

Bars Configuration ─> raw Bars ─> InterpolatedPrices

Rebalance Configuration ────────────> ImmediateSignal

ETF weight Signal + InterpolatedPrices + ImmediateSignal
                                  └──> PortfoliosDataNode
                                       ├──> PortfolioWeightsStorage
                                       └──> PortfoliosStorage
```

The Bars Configuration resolves the source bars table. The interpolation settings derive the
dynamic `InterpolatedPrices` storage identity. Neither source table data nor derived observations
are copied into the Portfolio Configuration.

## Consequences

- Portfolio calculation state and Job orchestration state each have one authoritative owner.
- The GUI must query the Job when it displays or edits schedule and compute settings.
- A Portfolio Configuration remains stable when its Job schedule, compute request, image, or latest
  JobRun changes.
- Phase 1 produces only Immediate-Signal analytical backtests and presents that limitation
  explicitly.
- Persistent `InterpolatedPrices` remains part of the portfolio graph and is not replaced by
  temporary portfolio-local forward filling.
- The schedule and compute ownership portion of ADR 0007 is superseded; its one-Job-per-signal
  configuration decision remains accepted until the signal configuration is normalized.
- Normalizing the existing Signal Job configuration requires a migration that preserves Job links
  while removing duplicated schedule, compute, and lifecycle columns.

## Acceptance Criteria

- The future Portfolio Configuration schema contains no schedule, compute, deployment, JobRun, or
  copied source-identity columns.
- One Portfolio Configuration is associated with at most one dedicated Job, and one Job executes
  exactly one Portfolio Configuration.
- Schedule and compute mutations write only the Main Sequence Job.
- Portfolio API responses obtain operational state from the Job and execution state from JobRuns.
- Creating or running a Phase-1 portfolio rejects any rebalance strategy other than
  `ImmediateSignal`.
- Tests prove that the resolved portfolio graph uses the selected signal, the Bars-derived
  `InterpolatedPrices`, and `ImmediateSignal`.
- User-facing documentation labels the result as an analytical observation-time backtest and
  states the execution assumptions and timing limitation.
