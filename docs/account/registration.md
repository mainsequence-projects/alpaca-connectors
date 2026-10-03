# Alpaca Account Registration

## Model

A registered Alpaca account consists of:

- one canonical ms-markets `Account`;
- one project-owned `AlpacaAccountDetails` row keyed by the same Account UID;
- zero or more immutable `AccountHoldingsSet` snapshots and their canonical holdings rows.

The stable `Account.unique_identifier` derives from the Alpaca account number and paper/live
environment. Rotating API credentials therefore does not create a new account identity.

## Credential Boundary

Every registration references exactly two Main Sequence Secrets, one for the API key and one for
the secret key ([ADR 0011](../adrs/0011_api_managed_alpaca_credential_secrets.md)). The detail row
stores, per Secret, its UID and display name, plus:

- `credential_source`: `managed` or `external`;
- `credentials_updated_at` and `credentials_updated_by_user_uid`;
- a non-reversible API-key fingerprint, which no response exposes.

**Managed** credentials come from the static site. The user types both keys; the API checks them
against Alpaca, then creates or overwrites the Secrets
`ALPACA_CONNECTORS__<Account.unique_identifier>__API_KEY` and `...__SECRET_KEY`. The application
owns them: rotation overwrites them in place and removing the registration deletes them. The
`ALPACA_CONNECTORS__` prefix is reserved and can never be selected as an external Secret.

**External** credentials are existing Secrets selected by name through the CLI, the Tau tools, or
the API's `external` credential shape. The application only reads them; rotation or removal never
modifies or deletes them.

Credentials resolve by Secret UID immediately before an Alpaca client is constructed, so renaming a
Secret or creating a duplicate name does not affect a registered account. Values are not cached,
returned, logged, or persisted outside Main Sequence Secrets. Only the HTTP create and rotate
endpoints accept values; the CLI and Tau tools accept Secret names only.

Alpaca must accept the credentials, and every held position must resolve, before any Secret,
Account, or holdings row is written. If a later write fails, Secrets created by that request are
deleted on a best-effort basis; because the names are deterministic, a retry reuses any Secret left
behind.

## CLI

Preflight the provider identity and current holdings without writes:

```bash
alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper \
  --plan-only
```

Register the account, every missing held asset, and the initial holdings snapshot in one flow:

```bash
alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper
```

Maintain the registration:

```bash
alpaca-connectors account list
alpaca-connectors account get <ACCOUNT_UID>
alpaca-connectors account update <ACCOUNT_UID> --account-name "Paper account"
alpaca-connectors account refresh <ACCOUNT_UID>
alpaca-connectors account remove <ACCOUNT_UID>
alpaca-connectors account remove <ACCOUNT_UID> --execute
```

Removal deletes the project binding and deactivates the shared Account. It retains historical
holdings, deletes managed credential Secrets, and keeps external ones.

Registrations created before Secret UIDs were stored must be backfilled once after the `0015`
migration; until then their credential resolution fails with an instruction to run it:

```bash
alpaca-connectors account backfill-secret-uids
alpaca-connectors account backfill-secret-uids --execute
```

## Holdings

```bash
alpaca-connectors holdings list --account-uid <ACCOUNT_UID>
alpaca-connectors holdings capture --account-uid <ACCOUNT_UID>
alpaca-connectors holdings capture --account-uid <ACCOUNT_UID> --execute
```

Account registration always creates an initial holdings snapshot and enforces the same hard
registry as every later holdings capture. Every non-zero position is resolved to the Alpaca Asset
catalog and stored as `ALPACA::<alpaca_catalog_asset_uuid>`. Missing Alpaca assets are registered
before the Account or holdings set is written, for every supported Alpaca asset class. The whole
position set uses one unfiltered Alpaca catalog request, one set-based Main Sequence identity
query, and one bulk registration operation per row type; it never performs one backend operation
per position. OpenFIGI enrichment is optional. If a position lacks a valid Alpaca UUID, Alpaca cannot return its asset
record, or the asset catalog returns a conflicting identity, the operation reports the affected
symbol and identifiers and writes no partial Account or snapshot. Cash is stored against the
shared ms-markets currency Asset `USD`. A dry run reports when that row is missing; execution
idempotently ensures the built-in `currency` AssetType and `USD` Asset before publishing holdings.
It does not create an Alpaca asset-detail row or a currency-pair (`CurrencySpot`) record. There is
no option to skip or relax initial holdings capture.

For crypto, Alpaca positions and its Asset catalog can expose different UUIDs for the same pair.
The connector matches the position to the already-loaded catalog by normalized symbol (for example,
`BTCUSD` resolving to catalog asset `BTC/USD`) and stores the immutable catalog Asset UUID. For
non-crypto assets, the position UUID and catalog UUID must match exactly.
