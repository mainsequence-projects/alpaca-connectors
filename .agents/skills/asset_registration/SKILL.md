---
name: alpaca-asset-registration
description: Register exact Alpaca US equity symbols as canonical Main Sequence Assets, using a registered Account's Secret names and optional OpenFIGI enrichment.
---

# Alpaca Asset Registration

Use this skill to inspect registered Alpaca-backed Assets or to plan and execute exact-symbol
registration. In a CodeRepository Executor session, use `alpaca_query_assets` and
`alpaca_register_assets`. In a shell, use the installed `alpaca-connectors` command.

## Identity And Inputs

- Require a registered `account_uid`; its stored Main Sequence Secret names resolve provider
  credentials at runtime. Never request or accept API-key values.
- Require explicit symbols. Asset registration never expands ETF seeds or extracts components.
- Treat Alpaca's immutable asset UUID as authoritative:
  `Asset.unique_identifier = ALPACA::<alpaca_asset_uuid>`.
- Require one connector-owned `AlpacaAssetDetails` row for each Alpaca Asset.
- Preserve Alpaca `status` and `tradable` as facts, not eligibility gates.
- Treat OpenFIGI as optional enrichment. Missing FIGI data or an OpenFIGI outage never blocks an
  otherwise valid Alpaca registration.

For Universe-sized work, resolve the all-status Alpaca catalog once, use bulk MetaTable writes,
and publish all `AssetSnapshot` rows through one updater execution. Never issue one provider or
Main Sequence request per symbol.

## Workflow

1. Query existing assets with `alpaca_query_assets` operation `list` or `get` when needed.
2. Call `alpaca_register_assets` with operation `plan` and a request containing `account_uid`,
   `symbols`, and optional `timeout`.
3. Report missing Alpaca symbols and OpenFIGI warnings separately.
4. Execute only when the user requested registration, using the identical request with operation
   `execute`.
5. Verify returned canonical Asset UIDs and any symbols that were not registered.

Shell equivalents:

```shell
alpaca-connectors asset register --account-uid <ACCOUNT_UID> --symbols NVDA,AAPL
alpaca-connectors asset register --account-uid <ACCOUNT_UID> --symbols NVDA,AAPL --execute
```

Stop if the account does not exist, its Secret references cannot resolve, Alpaca supplies no
immutable asset UUID, or a symbol has neither one current-catalog identity nor one unique stored
Alpaca identity.
