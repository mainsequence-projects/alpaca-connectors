# ADR: API-Managed Alpaca Credential Secrets

## Status

Proposed on 2026-10-03. Implemented in the connector and the static site on 2026-10-03; acceptance
still requires the platform checks listed under
[Verification Before Acceptance](#verification-before-acceptance).

## Context

Today an Alpaca account is registered by selecting two existing Main Sequence Secrets, one holding
the API key and one holding the secret key. The static site lists Secret names through
`GET /v1/accounts/secret-references`, and `POST /v1/accounts` stores only the selected names on
`AlpacaAccountDetails`. Every later Alpaca call resolves those names to values.

That keeps credential values out of the application, but it makes the site depend on Command Center:

- a site user must open Command Center, create two Secrets, name them and make them visible to the
  API runtime before the registration form can do anything;
- the Secrets belong to whoever created them, so their lifecycle (rotation, deletion) is not
  connected to the account registration that depends on them;
- Secrets are referenced by name, so renaming one breaks the account, and a second Secret with the
  same name makes every resolution fail as ambiguous (`src/alpaca_connectors/platform_secrets.py`);
- the registration form cannot check the keys until the user has already created both Secrets.

The installed SDK (`mainsequence` 9.0.2) supports `Secret.create(name=..., value=...)`. The create
response may omit `uid` and `value`, so the new Secret must be read back by its exact name. Secret
names are unique within an Organization Environment. SDK calls made inside the FastAPI release are
authenticated as the API's runtime principal, not as the human caller in `request.state.user`.

## Decision

The static site collects the Alpaca API key and secret key directly. The API validates them
against Alpaca, then creates and owns the two Main Sequence Secrets that hold them. A site user never
has to open Command Center to register or rotate an account.

### Credential sources

`AlpacaAccountDetails` keeps referencing exactly two Secrets. A new `credential_source` column says
who owns them:

- `managed`: created by this API from values submitted by a user. The API owns their whole
  lifecycle: it creates, rotates and deletes them.
- `external`: existing Secrets selected by name (today's behaviour). The API reads them but never
  modifies or deletes them.

The static site uses only `managed`. `external` stays for the CLI and Tau tools (see
[Surfaces](#surfaces)).

### Secret naming

Managed Secret names are derived from the account identity, so they are unique and stable across
retries:

```text
ALPACA_CONNECTORS__<Account.unique_identifier>__API_KEY
ALPACA_CONNECTORS__<Account.unique_identifier>__SECRET_KEY
```

For example `ALPACA_CONNECTORS__010203ABCD__ALPACA_PAPER__API_KEY`. The `ALPACA_CONNECTORS__`
prefix is reserved for managed Secrets. Following the access-control rules, the API first looks the
name up and creates it only when it is missing. If the name exists, the API updates its value. If
the name exists but the API runtime cannot read it, registration fails with a conflict and writes
nothing.

### Storage

An Alembic revision adds these columns to `AlpacaAccountDetails`:

- `api_key_secret_uid` and `secret_key_secret_uid`;
- `credential_source` (`managed` | `external`);
- `credentials_updated_at` and `credentials_updated_by_user_uid` (the human caller from
  `request.state.user_uid`, or null for CLI and Tau writes).

`api_key_secret_name`, `secret_key_secret_name` and `api_key_fingerprint` stay as display and audit
fields. All credential resolution switches to `Secret.get_by_uid`, so renaming a Secret or creating
a duplicate name no longer breaks an account. Revision `0015` adds the UID columns as nullable and
gives `credential_source` a permanent server default of `external`. That default labels existing
rows and keeps the previous release able to insert registrations while the new code rolls out, as
the MetaTables rule that each schema change stay compatible with the previous release requires. The
order is: run `0015`, deploy the code, then run the backfill. Those rows are backfilled once by the reviewed
`alpaca-connectors account backfill-secret-uids --execute`, which resolves their stored names to
UIDs in one set-based update; until then their resolution fails with that instruction. A follow-up
revision makes the UID columns non-null once the backfill has run in every environment. There is no
name-based fallback.

### Registration order

`POST /v1/accounts` with managed credentials runs in this order:

1. Validate the request. Both values are required, non-empty, free of whitespace, distinct and
   held as `SecretStr`.
2. Build an Alpaca client from the values in memory and read the account and its positions. If
   Alpaca rejects the keys, return `400` and write nothing, including no Secrets.
3. Derive `Account.unique_identifier`, resolve every held position and register missing assets
   (today's behaviour; asset rows are shared and idempotent). If a position fails to resolve, write
   no Secret, Account or snapshot.
4. Create or update both managed Secrets, then read back their UIDs by exact name.
5. Upsert the `Account` and the detail row, and publish the initial holdings snapshot.

If step 5 fails, the API makes a best-effort attempt to delete any Secret that step 4 created in this
request. It reports a failed cleanup by Secret name only. Because the names are deterministic, a
retry reuses and overwrites any Secret left behind. Re-registering an identity with external
Secrets deletes the managed Secrets it previously owned.

`POST /v1/accounts/registration/preflight` accepts the same body and performs steps 1–3 only. Its
response lists the Secret names that execution would create or update.

### Rotation, edits and removal

- `PATCH /v1/accounts/{uid}` accepts an optional `credentials` object containing both new values.
  Changes to the name or the active flag never read, validate or write credentials.
- New credentials must resolve to the registered `alpaca_account_id`; otherwise the API returns an
  error and changes nothing. For a `managed` account it updates both Secret values in place. For an
  `external` account it creates managed Secrets, switches the references to them and sets
  `credential_source = managed`, leaving the external Secrets untouched.
- Removing a registration deletes the detail row and deactivates the Account (today's behaviour),
  then deletes the account's managed Secrets. External Secrets are never deleted. If deleting a
  Secret fails, the removal still succeeds and returns a warning naming that Secret.

### Value boundary

- Inside Command Center the site does not call the API directly: the SDK transport passes the
  request, including the values, to the host window with `postMessage`, and the host makes the
  authenticated call. The host is therefore part of the value boundary.
- Credential values exist only in the request body and in process memory while the request runs.
  They are never persisted outside Main Sequence Secrets, returned in a response, logged, placed in
  an exception message or URL, or cached.
- The app installs a `RequestValidationError` handler that returns only `loc`, `msg` and `type`
  for every validation error. FastAPI's default 422 response echoes the rejected input, which would
  send a key back to the browser; without any handler, the SDK's uncaught-exception logger also
  writes that input to the logs. Both were reproduced before the handler was added.
- Secret create and update failures are re-raised without their SDK cause, because an SDK error for
  those requests may describe a request body that contains the value.
- Alpaca authentication failures map to a fixed `alpaca_credentials_rejected` error and do not pass
  provider text through.
- Account responses expose `credential_source`, the two Secret names and UIDs,
  `credentials_updated_at` and `credentials_updated_by_user_uid`. They never expose values or the
  API key fingerprint, which stays an internal audit field.

### Surfaces

- **Static site:** the registration and rotation forms use two password-type inputs with
  autocomplete off. The values live only in component state and are cleared on submit, cancel and
  unmount. They never go into a URL, browser storage or the persisted view state. The form runs the
  preflight before registering and shows the Secret names that will be created. The site no longer
  calls `GET /v1/accounts/secret-references`.
- **HTTP API:** accepts a credentials union, either `{"source": "managed", "api_key", "secret_key"}`
  or `{"source": "external", "api_key_secret_name", "secret_key_secret_name"}`.
- **Tau tools and agent sessions:** never accept credential values, because tool inputs persist in
  agent transcripts. Their schemas come from the separate `AccountSecretNameRegistrationRequest`
  and `AccountSecretNameUpdateRequest` models, so the managed shape cannot appear in a tool schema.
  They send users who want managed credentials to the site.
- **CLI:** keeps `external` registration by Secret name. A CLI path for managed values is out of
  scope.
- **Selecting a managed Secret as external is rejected:** the `ALPACA_CONNECTORS__` prefix is
  reserved, so another registration's rotation or removal can never break an account.

## Consequences

- A site user can register, check and rotate an Alpaca account without opening Command Center.
- The application now writes credential values to Main Sequence, and its HTTP boundary handles
  them. The validation-error handler, the absence of logging and the response models become
  security-relevant code with dedicated tests.
- Each managed Secret's lifecycle follows the account registration that owns it, so removing an
  account no longer leaves credentials behind.
- Resolving by UID removes the failures caused by renamed and ambiguous Secrets for every account,
  managed or external.
- Any user allowed to call the API release can create, rotate and delete managed credentials. This
  ADR adds no per-account ownership; who may call the API is still set by the release's access policy.

## Verification Before Acceptance

These platform behaviours are assumed above but not yet proven. Each must be checked against a live
Organization Environment and its result recorded here before the status becomes Accepted:

1. The API runtime principal is allowed to create, update and delete Secrets.
2. A Secret created by the API runtime can be read by every runtime that resolves account
   credentials: the API itself and the bars, signal, portfolio and holdings Jobs. If any Job runs as
   a different principal, the API must grant that principal or its team `view` on each managed Secret
   at creation, and this ADR must name it.
3. `Secret.patch` (or the SDK's equivalent) updates a value in place. If it does not, rotation
   becomes delete-then-create under the same name, run only after the new pair has been validated
   against Alpaca. The window during which Jobs may fail must be documented.
4. Reading a Secret back by exact name immediately after `Secret.create` returns its UID.

## Implementation Notes

Implemented on 2026-10-03:

- `src/alpaca_connectors/platform_secrets.py` and `src/alpaca_connectors/account/credentials.py`: UID resolution, managed Secret
  create/update/delete, reserved-prefix guard, and the fixed Alpaca rejection error.
- `src/alpaca_connectors/account/services.py`: registration order and cleanup, plan output (`credential_source`,
  `secret_writes`), rotation, managed-to-external switching, removal, and the backfill.
- Revision `0015_add_account_credential_secret_references`.
- `alpaca_connectors.api.app`: the `credentials` union, agent-only request models, the redacted 422 handler, the
  `alpaca_credentials_rejected` mapping, and the caller's user UID on create and update.
- `AGENTS.md`, `README.md`, `docs/`, and `.agents/skills/account_workflow/` describe the new model.
- `src/alpaca_connectors/settings.py`: the unused `get_alpaca_api_key` / `get_alpaca_secret_key` global fallbacks and
  their tests are deleted.

Still open before acceptance: the four platform checks above, `metatables migrations upgrade` to
`0015` with the backfill in each environment, and the follow-up non-null revision.
