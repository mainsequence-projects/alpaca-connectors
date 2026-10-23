# Operator Guide

Rules for people and coding agents who run the `alpaca-connectors` CLI, the API and the
repository's Jobs against a Main Sequence environment. They moved here from `AGENTS.md` on
2026-10-14, because TAU loads `AGENTS.md` into every Alpaca Agent session and the Agent cannot use
a shell or the CLI. The Agent's own runtime instructions are `.tau/SYSTEM.md`; its tools and
skills are described in [the Agent page](../agent.md).

## Supported Operator Surfaces

- installed CLI: `alpaca-connectors`
- the installable `alpaca_connectors` package under `src/alpaca_connectors/`; the wheel ships
  only this top-level package
- thin FastAPI surface in `alpaca_connectors.api` (`src/alpaca_connectors/api/`); `api/app/main.py`
  is only the deployed resource's path-stable entry point and is not part of the wheel
- the API's MCP server at `/mcp` (`alpaca_connectors.api.app.mcp_server`), which serves the
  structured project tools in `alpaca_connectors.agent_tools` to the Harness Agent (ADR 0015)
- the Harness Agent entry point `api/agent/main.py` (`ms_tau_sdk.create_app()`), not part of the
  wheel
- repository-local scheduled-job entrypoints under `src/jobs/`: scripts executed by path, not
  package modules; their paths are bound to live platform Jobs, so do not move them

## Asset Registration

- use `alpaca-connectors asset register`
- require `--account-uid`; provider access opens the keys stored encrypted on that registered
  Alpaca account, and the workflow never accepts credential values or global fallback credentials
- dry run is the default; pass `--execute` only when you want to write missing assets into Main
  Sequence
- use required `--symbols` for exact symbol registration; asset registration does not expand ETF
  or other provider-derived seeds
- Alpaca's immutable asset UUID is authoritative: write
  `Asset.unique_identifier = ALPACA::<alpaca_asset_uuid>` and a required project-owned
  `AlpacaAssetDetails` row
- OpenFIGI is optional enrichment; an unmatched FIGI or OpenFIGI outage is reported as a warning
  and never blocks registration
- symbols absent from Alpaca's current catalog may reuse one exact, previously registered
  canonical Alpaca Asset/detail identity; symbols with neither a current catalog identity nor one
  unique registered identity are reported and not registered
- Alpaca `status` and `tradable` are stored provider facts, not registration gates; resolve exact
  symbol sets from one all-status bulk catalog request, never one provider request per symbol

## Asset Universes And Holdings Categories

- maintain extraction targets with `alpaca-connectors universe-source` CRUD
- treat source preview as read-only; it never creates or infers a registered universe
- register an Asset Universe explicitly through `POST /v1/universes`; creation stores required
  `source_uid` and `asset_category_uid` foreign keys and performs no extraction
- use `alpaca-connectors universe run <UNIVERSE_UID>`
- dry run is the default; its plan identifies missing Alpaca-backed assets as work to perform
- Run follows the registered links and requires an Alpaca `account_uid` as execution input only;
  it registers missing constituents and replaces category membership only after every extracted
  symbol resolves through Alpaca, then publishes that exact extraction as one observation through
  the connector-owned `AlpacaETFHoldingsSignal`
- Run delegates category replacement to the `ms-markets>=1.0.3`
  `AssetCategory.replace_memberships` API, which bulk-upserts the complete desired membership and
  removes stale memberships with one delete; never execute one MetaTable request per constituent
- never persist or infer an Alpaca account on `AssetUniverse`; it represents the ETF holdings
  source and materialized weights, not a brokerage-account assignment
- never store source identity or lifecycle state in `AssetCategory.metadata_json`, and never infer a
  source, provider, universe, or category from another resource
- resolve current-catalog misses through one set-based lookup of existing canonical Alpaca
  Asset/detail rows; only symbols with neither a current catalog identity nor one unique registered
  identity are blockers, and existing category membership remains unchanged for those blockers
- `AlpacaETFHoldingsSignalConfig` contains exactly `universe_uid` and `account_uid`;
  `universe_uid` defines the stable signal business identity while `account_uid` remains serialized
  updater/runtime configuration and is excluded from `_signal_uid_payload()`
- signal rows use the built-in `SignalWeightsStorage` grain
  `(time_index, signal_uid, asset_identifier)`; neither account UID nor Universe UID is added as a
  storage column or index dimension
- every successful update publishes one complete batched observation, including when weights did
  not change; its timestamp records when extraction observed the weights and does not assert their
  exact economic effective time

## Price Updates From Alpaca

- maintain reusable definitions with `alpaca-connectors market-data bar-configuration` CRUD
- each definition selects one registered account, one migrated frequency/feed/adjustment profile,
  and exactly one asset source: explicit `assets`, an active `universe`, or recent
  `account_holdings`
- for account holdings, the configured Account UID resolves the newest stored holdings set inside
  the inclusive trailing 30-day window; the resolver never captures holdings as a side effect
- run `alpaca-connectors market-data update --configuration-uid <CONFIGURATION_UID>` for a dry-run
  resolution and add `--execute` to publish
- for one asset, the retained shorthand is configuration-backed, for example
  `alpaca-connectors asset IVV update_prices daily --configuration-uid <CONFIGURATION_UID>`
- use `update_prices` as the asset price-update action
- the shorthand verifies that the ticker and period belong to the stored configuration; it never
  constructs an ephemeral configuration
- dry run is the default; pass `--execute` to call `node.run()`
- update actions never accept a dataset UID, account override, bar-profile override, or asset-scope
  override
- only migrated dataset profiles are selectable; requests do not dynamically create schemas
- resolve Asset identities and provider details with set-based queries, and send Alpaca bar requests
  in symbol batches rather than one backend request per Asset

## Operational Notes

- source `.env` and export `MAINSEQUENCE_AUTH_MODE=jwt` before live `mainsequence`, `metatables`,
  or `alpaca-connectors` runs that need authenticated platform access; the SDK keeps the session
  in the operating-system credential store, never in `.env`
- before live platform checks, run `mainsequence refresh-token`
- account registration stores the Alpaca keys encrypted on the account row
  ([ADR 0017](../adrs/0017_encrypted_account_credentials.md)): `managed` when a person entered
  them, `imported` when they were copied once from Main Sequence Secrets selected by name; live
  holdings and price updates open them at execution time with the
  [account key Secret](#account-key-secret)
- registrations that predate revision `0018` need the one-off `alpaca-credential-migration` Job
  (or `alpaca-connectors account migrate-credentials --execute`) once per Environment
- account registration and holdings capture resolve the whole position set from one Alpaca catalog
  request and use bulk Main Sequence identity lookup and registration operations
- if a stored bar configuration resolves an asset that is missing or ambiguous in Alpaca, fix its
  source or register the asset correctly instead of loosening the resolver
- if `mainsequence code-repository current --debug` reports that the local SDK is behind the
  latest GitHub version, report it; run `mainsequence code-repository update-sdk --path .` only
  when the user explicitly requests the SDK update
- MetaTables come from the `mainsequence-metatable` client, not from the SDK: import table,
  migration, and updater interfaces from `metatables` and follow the copied
  `.agents/skills/metatables/` skills
- hosted schema changes are applied only by the migration Job in
  `.mainsequence/workflows/alpaca-connectors-api.yaml` (`src/jobs/migrate_alpaca_connectors.py`;
  ADR 0012), which runs from the candidate image before the API deploys (`needs: [migrate]`) and
  applies only this project's provider; keep every deployed resource that reads project tables in
  that file with its own `needs: [migrate]` deploy step; the `harness_agent` deploys after the API
  (`deploy_agent` needs `deploy_api`), so a failed migration stops it too; the two bars Jobs in
  their own files stay outside the graph with automatic redeployment disabled;
  the ms-markets schema is migrated only by the ms-markets deployment's `migrate-markets` Job; never
  run `metatables migrations upgrade` or `downgrade` against a hosted environment from a developer
  or agent session
- the project uses two MetaTables catalog namespaces: `alpaca-connectors` for this provider's
  Alembic registry, and `mainsequence.markets` for the project's tables and the ms-markets tables
  it writes; a person owns each namespace, and the owner grants each workload its MetaTables role
  (`data_viewer`, `data_writer` or `editor`, never `owner`), directly or through the Environment's
  Team (for example `alpaca-connectors-production`) for the roles its members share
  ([Table Access And MetaTables Roles](jobs.md#table-access-and-metatables-roles)). Every
  workflow resource that uses MetaTables declares
  `access.branches: [{repository: MetaTables, policy: reader}]`; the `harness_agent` uses only the
  API's MCP and declares no MetaTables access
- during development apply revisions to the local runtime, which has no ms-markets deployment, with
  `metatables --local migrations upgrade --provider msm_migrations:migration head`, then
  `metatables --local migrations upgrade --provider alpaca_connectors.migrations:migration head`; every revision must keep
  the previously deployed release working (add first, drop or rename in a later release)
- every launcher under `src/jobs/` except the migration Job starts with
  `alpaca_connectors.migrations.schema_gate.skip_if_schema_behind`

## Account Key Secret

Every Alpaca account's API key and secret key are stored in application-encrypted columns of the
account table ([ADR 0017](../adrs/0017_encrypted_account_credentials.md), MetaTables ADR 0025).
The connector seals and opens them with a keyring held by one Environment Secret,
`ALPACA_CONNECTORS_DATA_KEY`, which the workflow files grant as `value_reader` to the API, the bars
Jobs, the signal Job and the credential migration Job. Without it those workloads fail every
Alpaca call with `credential_key_unavailable`; nothing is ever written or read in plaintext.

Create the Secret once per Environment, before the first registration or the credential
migration. The command prints the keyring; store its output as the Secret's value in Command
Center or with the CLI, and never commit or log it:

```bash
.venv/bin/metatables encryption new-keyring
```

```bash
mainsequence secrets create ALPACA_CONNECTORS_DATA_KEY
```

Rotate the key in three steps. `rotate` reads the current ring through the signed-in session and
prints the same ring with a new active key, so rows written under the old key still open; after
every row has been rewritten the old key can be dropped from the ring:

```bash
.venv/bin/metatables encryption rotate ALPACA_CONNECTORS_DATA_KEY
```

Store the printed ring as the Secret's new value, then rewrite the account table under the active
key with the table's MetaTable UID (`alpaca_connectors__acct_alpaca`; `--dry-run` counts the rows
still under old keys):

```bash
.venv/bin/metatables encryption re-encrypt --table <ACCOUNT_TABLE_UID>
```

Finally store the ring without the old key. A lost keyring loses every stored key: the platform's
Secret backup is the recovery path, and the keys can always be entered again on the site.

Registrations made before revision `0018` keep their keys in the Main Sequence Secrets they
referenced until the one-off `alpaca-credential-migration` Job runs. Start it with a dry run, read
its summary (counts, Secret names and reasons, never values), then run it with `--execute`; it is
idempotent and retries the deletion of managed Secrets it could not delete. A registration it
blocks (fingerprint mismatch, unreadable Secret) is repaired by rotating its keys on the site.
