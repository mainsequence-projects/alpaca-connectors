# ADR: Universe-Backed Alpaca ETF Signal

## Status

Accepted

## Context

The generic `etfhextractor.ETFHoldingsSignal` owns a ticker/URL extraction workflow. It cannot own
this application's registered `AssetUniverse`, registered Alpaca accounts, Secret-name resolution,
provider-native asset registration, or linked `AssetCategory` materialization without reversing
the package dependency.

The application needs every successful Universe execution to save the weights it just observed as
an ms-markets signal. The account used to access Alpaca must be reproducible runtime configuration,
but changing the account must not create a different business signal for the same Universe.

## Decision

This repository owns `src.portfolios.AlpacaETFHoldingsSignal`, a `SignalWeights` producer. It reuses
the built-in `SignalWeightsStorage`; it does not define another signal table.

Its complete serialized configuration is:

```text
AlpacaETFHoldingsSignalConfig
  universe_uid
  account_uid
```

The runtime configuration, updater identity, and stored signal identity have different roles:

```text
Producer runtime input              = universe_uid + account_uid
Canonical SignalWeights update_hash = shared ms-markets SignalWeights update
Final signal_uid                    = AlpacaETFHoldingsSignal class + universe_uid
Stored signal row                   = time_index + signal_uid + asset_identifier -> signal_weight
```

The actual ms-markets updater model is `SignalWeightsConfiguration`, a
`TimeIndexTableUpdateConfig`. Its nested `signal_configuration` contains the connector's
`AlpacaETFHoldingsSignalConfig`, but ms-markets marks that field as hash-excluded so all Signal
producers use the canonical shared `SignalWeights` updater and output table. The dedicated
`AlpacaETFSignalJobConfiguration` row durably stores `universe_uid` and `account_uid` and
reconstructs the producer for each JobRun.

`account_uid` is present on `signal_configuration` so a scheduled update can resolve the account's
Alpaca Secret names. `_signal_uid_payload()` excludes it. It never becomes a
`SignalWeightsStorage` column or index dimension. The Job schedule is also not updater
configuration: it determines when a JobRun starts.

## Update Flow

```text
AlpacaETFHoldingsSignal.update()
  -> resolve AssetUniverse -> UniverseSource + AssetCategory
  -> extract holdings once through etfhextractor
  -> fetch one all-status Alpaca asset catalog through the configured account
  -> bulk resolve/register every constituent
  -> bulk replace AssetCategory memberships
  -> return one DataFrame batch containing the complete normalized weights
  -> SignalWeights publishes that batch to canonical SignalWeightsStorage
```

Every successful pass emits a new observation, even when its weights equal the previous
observation. There is no daily throttle, unchanged-weight suppression, session-close rewriting, or
backdating in this producer.

The `time_index` is when this application observed the extracted provider response. It is not a
guarantee that the provider weights became economically effective at that exact instant. Consumers
must treat the series as observed snapshots, not perfect point-in-time constituent history.

## Consequences

- Universe Run now materializes both category membership and weights from the same extraction.
- `AssetCategoryMembership` remains membership-only; weights live only in `SignalWeightsStorage`.
- All connector signals share the canonical ms-markets TimeIndex updater identity; `signal_uid`
  separates their observations in storage.
- The generic extractor signal remains unchanged in `etfhextractor`.
- Portfolio construction uses the connector-owned signal and accepts `universe_uid` plus
  `account_uid`, instead of duplicating ticker, provider URL, and holdings filters.
- A prepared read-only Universe plan may be attached transiently to avoid a second extraction in
  the same process. It is not serialized and does not affect either updater or signal identity.
