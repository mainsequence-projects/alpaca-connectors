# ADR 0017: Alpaca Account Keys Stored As Application-Encrypted Columns

## Status

Accepted on 2026-10-24. Supersedes the Secret-based parts of
[ADR 0011](0011_api_managed_alpaca_credential_secrets.md) (managed and external Secrets, Secret
UIDs, the backfill), the Secret-reference listing of
[ADR 0015](0015_agent_tools_on_the_api_mcp_and_site_key_entry.md), and the sentence of
[ADR 0014](0014_sdk_request_identity.md) that names Secret writes among the API's platform calls.
The identity rules of ADR 0014 and the site key-entry flow of ADR 0015 are unchanged.

Implementation status: implemented in the connector; the credential migration Job has to run once
per Environment after revision `0018`, and a later revision drops the transition columns (see
[Transition](#transition)).

## Context

Since ADR 0011 an Alpaca account registration referenced two Main Sequence Secrets, one per key.
The API created `managed` Secrets from keys a person entered (`ALPACA_CONNECTORS__<Account
.unique_identifier>__API_KEY` and `__SECRET_KEY`), or stored the names and UIDs of `external`
Secrets a person selected, and every Alpaca call read them back by UID as the workload User.

That design kept key values out of the application's tables, but it had costs that grew with
every account:

- the application wrote, rotated and deleted platform Secrets, so two Secrets per account
  accumulated in each Environment, named after the account, visible to every Secret reader;
- every workload that builds an Alpaca client (the API, the bars Jobs, the signal Job) needed
  `value_reader` on every one of those Secrets, which the platform grants per Secret: renaming the
  API's workflow key, or adding a Job, meant re-granting each account's Secrets by hand
  (ADR 0015);
- external Secrets belonged to whoever created them, and the reserved prefix, the UID resolution
  and the one-time backfill existed only to keep names from breaking accounts;
- the site needed a Secret picker and the API a `GET /v1/accounts/secret-references` route.

MetaTables 0.1.55 ships application-encrypted columns (MetaTables ADR 0025):
`metatables.EncryptedString(secret="<SECRET NAME>")` declares a text column whose values the
client seals in the application process with AES-256-GCM under a keyring stored in one
Environment Secret per application. The API, the Admin, the Analyst, raw SQL and the database see
only envelopes; compiled statements through the model's column encrypt bound values; a process
that cannot read the Secret fails closed and never writes plaintext.

## Decision

An account's keys live on its `AlpacaAccountDetails` row, in two encrypted columns, under one
application Secret. Platform Secrets no longer hold account keys.

### Storage

- `api_key` and `secret_key` are `EncryptedString(secret="ALPACA_CONNECTORS_DATA_KEY")` columns,
  nullable, labelled and described like every other column. Revision `0018` adds them as plain
  `TEXT`; the column type encrypts on write and decrypts on read, so the revision needs no key.
- `api_key_fingerprint`, `credential_source`, `credentials_updated_at` and
  `credentials_updated_by_user_uid` stay. `credential_source` is `managed` for keys a person
  entered through the API or the site, and `imported` for keys copied once from Main Sequence
  Secrets selected by name. The legacy value `external` marks a registration made before `0018`
  whose keys the migration has not copied yet.
- Equality on an encrypted value is meaningless, so the columns are never indexed, keyed or
  filtered. The fingerprint remains the only way to recognise a key.

### The key Secret

`ALPACA_CONNECTORS_DATA_KEY` is one Environment Secret per Environment. Its value is a keyring
created with `metatables encryption new-keyring`; an operator stores it in Command Center or with
`mainsequence secrets create`. The connector never creates or changes it. Every workload that
writes or reads account keys declares it in its workflow file:

```yaml
access:
  secrets:
  - name: ALPACA_CONNECTORS_DATA_KEY
    policy: value_reader
```

That is the API (`alpaca`), the bars Jobs (`alpaca-bars-update`, `daily-stock-bars-holdings-ivv`),
the signal Job (`alpaca-etf-signal`, which registers universe constituents through the
configuration's account) and the credential migration Job. The schema migration Job, the portfolio
Job and the Agent do not read keys and are not granted the Secret. Granting a second workload is a
visible decision in the workflow file, not a per-account grant.

Rotation never needs a schema change: `metatables encryption rotate` adds a new active key to the
ring, `metatables encryption re-encrypt --table <uid>` rewrites every row under it, and the old
key is then dropped from the ring. The [operator guide](../operations/operator-guide.md#account-key-secret)
records the procedure.

### Write path

`POST /v1/accounts`, `PATCH /v1/accounts/{uid}` and the site accept key values exactly as before
(`ManagedAlpacaCredentials`, write-only `SecretStr` fields). The CLI and the Agent's
`alpaca_manage_account` tool accept Secret names only, as before, but a name is now a one-time
import: the API reads both Secrets once as its workload, validates the values with Alpaca, stores
them encrypted, and keeps neither name nor UID. The reserved `ALPACA_CONNECTORS__` prefix is still
refused as an import source, because those Secrets belong to the migration.

Alpaca must accept the keys, and every held position must resolve, before anything is written.
The keys then travel in the same upsert as the rest of the detail row; the model's column type
seals them when the statement is compiled, so a failed write leaves nothing behind and no cleanup
of half-written Secrets exists any more. Rotation validates the new keys against the registered
Alpaca account, then replaces the stored values and the fingerprint in one compiled update, which
also clears any Secret references the row still carried.

### Read path

`build_registered_account_client`, the bars update and asset registration read the two columns of
one registration with a compiled select and open them in the process, immediately before an
Alpaca client is built (`read_registered_alpaca_credentials`). A registration without stored
keys fails with `account_credentials_missing`, which names the migration Job; a process that
cannot read the Secret fails with the MetaTables error that names the Secret
(`credential_key_unavailable`). A stored value that is not an envelope is refused. There is no
plaintext fallback and no `Secret.get_by_uid`.

Registration reads (`GET /v1/accounts`, `GET /v1/accounts/{uid}`, the CLI, the Agent) select
every detail column except the two encrypted ones and add the read-only `credentials_stored`
flag, so a key value never enters a response, a log, a CLI print or an Agent transcript.

### Removal

Deleting a registration deletes the row, and the keys with it. Nothing remains in platform
Secrets. `DELETE` still returns `deleted_secrets` and `warnings`; they are empty for every
registration made or migrated under this decision.

### Surfaces

- `GET /v1/accounts/secret-references`, `SecretReferenceResponse` and the Agent's
  `secret_references` query operation are removed. Account responses lose the Secret names and
  UIDs and gain `credentials_stored`.
- The site keeps the value-entry flow only: it shows how the keys were stored and when they last
  changed, and the registration review no longer lists Secrets to write.
- Ownership is unchanged: `credentials_updated_by_user_uid` records the acting user (ADR 0014),
  and who may register, rotate or delete an account is still decided by the release's access
  policy, not per person. Per-person ownership is recorded, not enforced.

### Transition

Revision `0018` adds the encrypted columns and makes `api_key_secret_name` and
`secret_key_secret_name` nullable; the release that serves during the rollout keeps writing
them, and this release writes `NULL`. The four Secret reference columns stay until every
Environment has run the migration, then a later revision drops them together with
`src/alpaca_connectors/platform_secrets.py` and `account/legacy_secrets.py`.

The one-off `alpaca-credential-migration` Job (`src/jobs/migrate_account_credentials.py`, also
`alpaca-connectors account migrate-credentials`) copies every registration's keys. For each
registration without stored keys it reads the referenced Secrets as its workload (by UID, or by
name for rows that predate UID storage), checks the API key against the stored fingerprint,
writes both values through the model, sets `credential_source` (`managed` stays `managed`,
`external` becomes `imported`), clears the references and deletes the Secrets this application
created. External Secrets are never deleted. A fingerprint mismatch or an unreadable Secret blocks
only that registration, with a reason that names the Secret. The Job is idempotent: migrated rows
are skipped, and a row whose managed Secrets could not be deleted keeps its references so the next
run retries. It reports counts, names and reasons, never a value, and is a dry run unless started
with `--execute`.

Until the migration has run, a registration made before `0018` cannot build an Alpaca client:
its Alpaca calls fail with `account_credentials_missing`. Rotating such a registration stores the
new keys and deletes the managed Secrets it referenced, so rotation is the other way out.

## Consequences

- The application holds its own credentials, behind two gates: the table grant decides who reads
  the row, the Secret grant decides who reads the keys. Table readers, Organization admins, the
  Analyst, the Admin, SQL and backups see envelopes.
- One grant per workload replaces two grants per account and workload. Adding a Job that calls
  Alpaca is one line in its workflow file.
- A lost keyring loses every stored key; the platform's Secret backup is the recovery path, and
  the keys can always be entered again on the site.
- The value boundary of ADR 0011 (write-only fields, the redacted `422` handler, the fixed Alpaca
  rejection error, no logging) stays in force and now also covers the compiled statements: tests
  assert that an upsert or update leaves the process with envelopes, not values.
- The CLI, the Agent and the API keep accepting Secret names, so a key that already lives in a
  Secret can still be used without typing it; the Secret stays untouched and is read exactly once.

## Verification

- `tests/test_account_credentials.py`: the compiled update and the registration upsert carry
  envelopes under the injected keyring, never values; reads open them only in their own column;
  a missing or unreadable Secret fails closed naming the Secret; a non-envelope value is refused;
  imports read Secrets by exact name and keep no name.
- `tests/test_account_services.py`, `test_api_app.py`, `test_cli.py`,
  `test_account_key_requests.py`: registration, rotation, import, removal, responses, OpenAPI
  and CLI output carry no key value and no Secret column.
- `tests/test_account_credential_migration.py`: dry run, execution, fingerprint mismatch,
  missing Secret, rerun after a failed deletion, and the Job launcher's schema gate.
- `tests/test_schema_gate.py` and `test_agent_artifacts.py`: the workflow grants.
- Pending on a hosted Environment: creating the Secret, the `0018` rollout, one migration run per
  Environment, and the follow-up revision that drops the transition columns.
