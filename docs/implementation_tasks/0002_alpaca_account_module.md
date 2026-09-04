# Alpaca account storage and holdings

> **Status:** implemented locally. Revision `0002` adds Secret-name bindings and the user-managed
> universe-source table. Applying and verifying that revision in the platform remains an explicit
> deployment operation.

## Purpose

The `src/account/` capability registers an Alpaca trading account in the generic ms-markets
account graph. Account metadata refresh and holdings capture are separate operations.

| Layer | Responsibility |
| --- | --- |
| `src/account/credentials.py` | resolve named Main Sequence Secrets at the provider boundary |
| `src/account/alpaca_account_details.py` | project-owned account detail/current-state MetaTable |
| `src/account/services.py` | register, read, update, remove, and refresh account registrations |
| `src/holdings/services.py` | plan, capture, list, and read immutable holdings snapshots |
| `src/cli/account.py` and `src/cli/holdings.py` | thin CLI adapters |

## Identity and credentials

Alpaca's account number supplies stable identity across key rotation:

```text
<ACCOUNT_NUMBER>__ALPACA
<ACCOUNT_NUMBER>__ALPACA_PAPER
```

`AlpacaAccountDetails` stores `api_key_secret_name` and `secret_key_secret_name`, not the Secret
values. Values are resolved without caching immediately before creating an Alpaca client. A
non-reversible `sha256(api_key)[:16]` fingerprint is retained for audit correlation.

Paper/live is immutable because it participates in account identity. The mutable application
fields are display name, both Secret-name bindings, and active state.

## Storage

The project-owned `alpaca_connectors__acct_alpaca` table is one-to-one with `AccountTable.uid` and
stores Alpaca metadata, configuration, and the latest account financial state. Point-in-time
positions use canonical ms-markets primitives:

- `AccountTable`
- `AccountHoldingsSetTable`
- `AccountHoldingsStorage`
- `AssetTable`

Snapshots are immutable. Removing the application registration deletes only the project detail
row, deactivates the generic Account, and retains historical holdings.

## Migration

`src.migrations:migration` owns `AlpacaAccountDetails` and `UniverseSourceTable`. Apply revision
`0002` with:

```bash
mainsequence migrations upgrade --provider src.migrations:migration head
```

Runtime attachment consumes the migrated tables and does not create them.

## Account operations

Plan and register using Secret names:

```bash
alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper \
  --plan-only

alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper
```

Read and maintain registrations:

```bash
alpaca-connectors account list
alpaca-connectors account get <account-uid>
alpaca-connectors account update <account-uid> --account-name "Paper account"
alpaca-connectors account refresh <account-uid>
alpaca-connectors account remove <account-uid>
alpaca-connectors account remove <account-uid> --execute
```

## Holdings operations

```bash
alpaca-connectors holdings capture --account-uid <account-uid>
alpaca-connectors holdings capture --account-uid <account-uid> --execute
alpaca-connectors holdings list --account-uid <account-uid>
```

Account registration always registers missing held assets and creates an initial holdings snapshot.
Registration and later holdings capture are hard-registry: every non-zero position must resolve to
a registered ms-markets Asset before publication. Execution registers missing held assets from
Alpaca's immutable provider identity; OpenFIGI remains optional enrichment for US equities. Invalid
position identities and provider catalog identity conflicts block the operation with the affected
symbol and exact identifiers. Cash uses the shared canonical `USD` currency Asset: planning reports
when it must be ensured, and execution idempotently upserts the built-in currency AssetType and
`USD` Asset. It never creates Alpaca details or a CurrencySpot row for cash. No partial Account or
holdings snapshot is written and there is no relaxed capture mode.

## Live verification still required

A controlled paper-account run must verify account/detail identity, absence of credential values in
stored and returned data, one canonical holdings set, expected equity/cash rows, and an idempotent
rerun. This is execution evidence, separate from local implementation and schema review.
