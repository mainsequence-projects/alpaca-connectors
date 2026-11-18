# Alpaca Account Registration

## Model

A registered Alpaca account consists of:

- one canonical ms-markets `Account`;
- one project-owned `AlpacaAccountDetails` row keyed by the same Account UID;
- zero or more immutable `AccountHoldingsSet` snapshots and their canonical holdings rows.

The stable `Account.unique_identifier` derives from the Alpaca account number and paper/live
environment. Rotating API credentials therefore does not create a new account identity.

## Credential Boundary

A registration stores its Alpaca API key and secret key on the detail row, in the `api_key` and
`secret_key` columns, which are MetaTables application-encrypted columns
([ADR 0017](../adrs/0017_encrypted_account_credentials.md)). The connector seals the values in its
own process under the keyring held by the `ALPACA_CONNECTORS_DATA_KEY` Secret, so the MetaTables
API, the Admin, the Analyst, SQL and the database only ever see envelopes. The row also carries:

- `credential_source`: `managed` when a person entered the keys through the API or the site,
  `imported` when they were copied once from Main Sequence Secrets selected by name;
- `credentials_updated_at` and `credentials_updated_by_user_uid`: when the keys last changed and
  the API caller who changed them, null for CLI and Tau writes;
- `owner_gateway_uid` and `owner_user_uid`: the account's owner, set by the first API
  registration and never changed by a rotation, `(uid, uid)` for a Main Sequence user
  ([ADR 0019](../adrs/0019_account_owners_vouched_for_by_trusted_applications.md));
- a non-reversible API-key fingerprint, which no response exposes;
- `credentials_stored` and `credentials_removed`, read-only flags in every response:
  `credentials_stored` is false once the owner removed the keys, when `credentials_removed` is
  true, and for a registration made before revision `0018` that the credential migration has not
  copied yet.

**Managed** keys come from the static site or the API's `managed` credential shape. The user types
both keys; the API checks them against Alpaca and stores them encrypted. Rotation replaces them in
place, removing the keys clears them, and deleting the account deletes the row with them.

**Imported** keys come from two existing Main Sequence Secrets named through the CLI, the Agent's
MCP tools, or the API's `external` credential shape. The API reads both Secrets once as its
workload, checks the values against Alpaca and stores them encrypted; it keeps neither the names
nor the UIDs and never modifies or deletes the Secrets. Names with the reserved
`ALPACA_CONNECTORS__` prefix cannot be imported: they belong to the credential migration.

The keys are opened immediately before an Alpaca client is constructed, in the process that
holds the Secret. Values are not cached, returned, logged, or persisted anywhere else. Only the
HTTP create and rotate endpoints accept values; the CLI and the Agent's MCP tools accept Secret
names only. An Agent instead starts an account key request, and the person types the keys on the
Alpaca site ([ADR 0015](../adrs/0015_agent_tools_on_the_api_mcp_and_site_key_entry.md)).

Alpaca must accept the credentials, and every held position must resolve, before any Account,
detail, or holdings row is written. The keys travel in the same upsert as the rest of the detail
row, so a failed write leaves nothing behind. A workload without the Secret fails with an error
that names the Secret; nothing is ever written or read in plaintext.

## CLI

The CLI imports the keys once from two existing Main Sequence Secrets. Preflight the provider
identity and current holdings without writes:

```bash
alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper \
  --plan-only
```

Register the account, every missing held asset, and the initial holdings snapshot in one Markets
call (see [Holdings](#holdings)):

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
alpaca-connectors account remove-keys <ACCOUNT_UID>
alpaca-connectors account remove-keys <ACCOUNT_UID> --execute
alpaca-connectors account delete <ACCOUNT_UID>
alpaca-connectors account delete <ACCOUNT_UID> --execute
```

`update` with both `--api-key-secret-name` and `--secret-key-secret-name` imports new keys the
same way; the new keys must authenticate as the registered Alpaca account.

Removing keys and deleting an account are separate actions
([ADR 0028](../adrs/0028_every_portfolio_belongs_to_one_account.md), decision 8):

- **`remove-keys`** clears the stored keys of the account's `ProviderAccountDetails` row. The
  owner, the account, its history, portfolios and configurations stay; nothing is disabled, and
  runs fail with the ordinary error until the owner registers keys again.
- **`delete`** deletes the account and everything it owns: its portfolios (retired, then deleted
  unless another account's history names them), its bars feeds, its signal definitions and their
  signals, its orders and trades, and its whole Markets history. It is refused, before anything
  changes, while another account's current target holds one of its portfolios. A registration
  made before revision `0018` takes the Secrets this application created with it, as
  `remove-keys` does.

Without `--execute`, each only reports what it would do.

Only the account's owner creates or changes its bars feeds, signal definitions and portfolios,
and nobody does on an account without an owner until it takes one at its next key registration. An external person cannot claim an account without an owner while enabled
configurations run with its keys.

Registrations created before revision `0018` still hold their keys in Main Sequence Secrets. The
one-off `alpaca-credential-migration` Job, or this command, copies them into the encrypted
columns, deletes the Secrets this application created and keeps external ones; until it has run,
such a registration cannot build an Alpaca client and reports `account_credentials_missing`:

```bash
alpaca-connectors account migrate-credentials
alpaca-connectors account migrate-credentials --execute
```

The output carries counts, Secret names and reasons, never a key value. See the
[operator guide](../operations/operator-guide.md#account-key-secret) for the Secret itself.

## Holdings

```bash
alpaca-connectors holdings list --account-uid <ACCOUNT_UID>
alpaca-connectors holdings capture --account-uid <ACCOUNT_UID>
alpaca-connectors holdings capture --account-uid <ACCOUNT_UID> --execute
```

Account registration always creates an initial holdings snapshot and enforces the same hard
registry as every later holdings capture. Every non-zero position is resolved to the Alpaca Asset
catalog and stored as `ALPACA::<alpaca_catalog_asset_uuid>`, for every supported Alpaca asset
class. The whole position set uses one unfiltered Alpaca catalog request; it never performs one
backend operation per position. The catalog is read as Alpaca sends it, so an asset on an exchange
(or with a class or status) the installed `alpaca-py` does not know yet keeps Alpaca's value instead
of failing the read; a catalog entry that is not an asset record is skipped and logged. If a
position lacks a valid Alpaca UUID, Alpaca cannot return its asset record, or the asset catalog
returns a conflicting identity, the operation reports the affected symbol and identifiers and
writes nothing.

Registration and capture then write to Markets in one ms-markets `registerAccount` call
(`POST /api/v1/account/registration/`, ms-markets ADR 0051), one Markets transaction that:

- creates each held asset, with its asset type, that Markets does not have yet, and writes an
  asset snapshot (name, ticker, exchange) only where it differs from the asset's latest one, a
  comparison Markets makes, not the connector;
- creates the shared ms-markets currency Asset `USD`, which cash is stored against, when it is
  missing (no Alpaca asset-detail row and no currency-pair `CurrencySpot` record);
- creates the Account or changes only its differing fields; a capture sends the Account's current
  name and no flags, so it leaves the Account as it is;
- writes the holdings set, replacing a snapshot at the same time, as a holdings publish always did.

This project's own rows follow the call: an `AlpacaAssetDetails` row for each held asset that has
none yet, keyed by the uid Markets answered, and, for a registration, the account's details row.
A registration of a known account therefore makes one Markets call (`registerAccount`) and, on this
project's tables, reads the account's details row and which held assets have detail rows, and
writes the account's details row. A capture makes two Markets calls (the Account read and
`registerAccount`) and the same two reads. The API answers a registration from what it wrote,
without reading it back. A dry run reports which assets and whether the `USD` Asset would be
created, and writes nothing. OpenFIGI enrichment is optional. There is no option to skip or relax
initial holdings capture.

Every write is idempotent. Markets answers a repeat with the same holdings at the same time without
writing, and the account's details row is an upsert on the account uid, so a caller that sends a
registration again after its own timeout, or twice at once, succeeds. An asset Markets already
stores with another asset type makes Markets refuse the whole call, and the API answers
`409 action_conflict` in its own words, naming neither the asset nor the stored type; nothing is
written. The `registerAccount` route exists from ms-markets 3.4.0, so the Environment's Markets
deployment must run 3.4.0 or later before this connector release.

Applications such as HunchAPI capture through the API's
[account sync](../api.md#account-sync), `POST /v1/accounts/{account_uid}/actions/sync`, which runs
the same capture and answers an observable operation they can read again. When the account has
a target in ms-markets, the sync also sizes it and allocates the snapshot to the account's funds
([ADR 0023](../adrs/0023_account_sync_sizes_and_allocates_funds.md)). The capture routes and the
Agent's `capture_holdings` publish the snapshot only.

For crypto, Alpaca positions and its Asset catalog can expose different UUIDs for the same pair.
The connector matches the position to the already-loaded catalog by normalized symbol (for example,
`BTCUSD` resolving to catalog asset `BTC/USD`) and stores the immutable catalog Asset UUID. For
non-crypto assets, the position UUID and catalog UUID must match exactly.
