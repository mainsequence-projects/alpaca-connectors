# Alpaca Account Registration

## Model

A registered Alpaca account consists of:

- one canonical ms-markets `Account`;
- one project-owned `AlpacaAccountDetails` row keyed by the same Account UID;
- zero or more immutable `AccountHoldingsSet` snapshots and their canonical holdings rows.

The stable `Account.unique_identifier` derives from the Alpaca account number and paper/live
environment. Rotating API credentials therefore does not create a new account identity.

## Credential Boundary

The application stores only:

- `api_key_secret_name`;
- `secret_key_secret_name`;
- a non-reversible API-key fingerprint.

The named Main Sequence Secrets are resolved immediately before creating an Alpaca client. Values
are not cached, persisted, accepted by CLI/API request fields, or returned. Create the Secrets in
the active Organization Environment before registration.

Main Sequence Secret collection queries return metadata only. The connector first resolves each
exact name to its Secret UID and then reads the UID-addressed detail resource to obtain the value.
It never interprets the intentionally redacted value on a list result as an empty Secret.

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
holdings.

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
