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

## CLI

Preflight the provider identity and current holdings without writes:

```bash
alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper \
  --plan-only
```

Register account metadata, optionally capturing initial holdings:

```bash
alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper \
  --capture-initial-holdings
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

Equity positions resolve to canonical Asset identifiers. The capture can register missing
FIGI-backed equities through the strict registration path. Unsupported asset classes and unresolved
symbols are reported. Cash uses an existing USD Asset and is never manufactured by this connector.
