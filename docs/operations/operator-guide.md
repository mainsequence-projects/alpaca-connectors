# Operator Guide

Rules for people and coding agents who run the `alpaca-connectors` CLI, the API and the
repository's Jobs against a Main Sequence environment. They moved here from `AGENTS.md` on
2026-10-14, because TAU then loaded `AGENTS.md` into every Alpaca Agent session and the Agent cannot
use a shell or the CLI; since TAU 2.0.26 the Agent reads only `.tau/`. The Agent's own runtime
instructions are `.tau/SYSTEM.md`; its tools and skills are described in
[the Agent page](../agent.md).

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
- use required `--symbols` for exact symbol registration; asset registration registers exactly
  those symbols and expands no list
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
- every registration and account sync links each asset to the trading calendar of its Alpaca
  class: `NYSE` for US equities and options, `CRYPTO_24_7` for crypto
  ([ADR 0026](../adrs/0026_assets_are_registered_with_their_trading_calendar.md)); ms-markets 4
  refuses a price update for an asset without one, and a missing calendar is stored first, by the
  connector's own `alpaca_connectors.assets.calendars.ensure_trading_calendar`
- once per Environment after the ms-markets 4 release, link the assets registered before it:
  `alpaca-connectors asset link-calendars` reports each calendar's assets and how many have no
  calendar, and `--execute` links only those; an asset that has a calendar is never written

## Portfolios

- a portfolio is built from weights an application provides
  ([ADR 0024](../adrs/0024_portfolios_from_provided_weights.md),
  [ADR 0027](../adrs/0027_one_portfolio_path.md)); it belongs to one account and follows one of
  that account's signal definitions
  ([ADR 0028](../adrs/0028_every_portfolio_belongs_to_one_account.md)); only the account's owner
  creates them, through `POST /v1/signal-definitions` and `POST /v1/portfolio-configurations`
  (or ADR 0024's `.../provided-weights` until the applications move); no CLI command or Agent tool
  creates one or gives it weights ([Portfolios](../portfolios/portfolios.md))
- once per Environment after the ADR 0028 release, `alpaca-connectors portfolio adopt` reports
  each portfolio not yet adopted: the ones it would give their account, a signal definition that
  keeps their signal and their own bars configuration, and the ones it would end because their
  bars run on an account their owner does not own; `--execute` does it, deletes the `portfolios`
  feeds nothing names any more, and reports any portfolio an account's current target holds
- the application registers every asset before naming it in weights; an unregistered asset, or
  one without Alpaca details, refuses the whole call
- inspect with `alpaca-connectors portfolio list` (`--account-uid`, `--signal-definition-uid`),
  `get` and `runs`; `run` starts one run on the
  `alpaca-portfolio` Job, and `update <uid> --no-scheduled` leaves a portfolio out of the
  02:00 UTC dispatch
- `alpaca-connectors portfolio delete <uid>` is a dry run; `--execute` retires the portfolio's
  `Portfolio` before deleting the configuration, and is refused while an account's current target
  names it: that account's owner changes the target first, never the connector
- retiring deletes nothing: the `Portfolio`, its series and the history of every account that
  referenced it stay, recorded under the account, whose delete deletes them
- a portfolio's bars come from its own bars configuration, which follows what its signal ever
  published, so new weights need no bars edit
- after a new bars profile is migrated, run `alpaca-connectors portfolio prepare-interpolated-prices`,
  then the same command with `--check-only`

## Price Updates From Alpaca

- maintain reusable definitions with `alpaca-connectors market-data bar-configuration` CRUD
- a feed belongs to one registered account and selects one migrated frequency/feed/adjustment
  profile and exactly one asset source: explicit `assets` or recent `account_holdings`; a
  portfolio's own configuration, with the `portfolios` source, is made with the portfolio
  (ADR 0028); every one writes the shared table of its profile
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
- removing keys and deleting an account are separate
  ([ADR 0028](../adrs/0028_every_portfolio_belongs_to_one_account.md)):
  `alpaca-connectors account remove-keys <uid>` clears the stored keys and keeps the owner and
  everything else; `alpaca-connectors account delete <uid>` reports what deleting the account
  would delete and what refuses it, and with `--execute` deletes the account and everything it
  owns, its whole Markets history included; another account's current target on one of its
  portfolios refuses it
- account registration stores the Alpaca keys encrypted on the account row
  ([ADR 0017](../adrs/0017_encrypted_account_credentials.md)): `managed` when a person entered
  them, `imported` when they were copied once from Main Sequence Secrets selected by name; live
  holdings and price updates open them at execution time with the
  [account key Secret](#account-key-secret)
- registrations that predate revision `0018` need the one-off `alpaca-credential-migration` Job
  (or `alpaca-connectors account migrate-credentials --execute`) once per Environment
- account registration and holdings capture resolve the whole position set from one Alpaca catalog
  request and write the account, the held assets Markets lacks and the holdings in one Markets
  `registerAccount` call, which needs ms-markets 3.4.0 or later on the Markets API
  ([registration](../account/registration.md#holdings))
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
  (`deploy_agent` needs `deploy_api`), so a failed migration stops it too; the bars Job and its
  daily dispatcher in their own file, `alpaca-bars-update.yaml`, stay outside the graph and every
  push promotes them, their schema gates covering the gap;
  the ms-markets schema is migrated only by the ms-markets deployment's `migrate-markets` Job; never
  run `metatables migrations upgrade` or `downgrade` against a hosted environment from a developer
  or agent session
- every table this project owns, including its instances of the Markets time series, is in its
  own `alpaca-connectors` namespace (ms-markets ADR 0047); a person owns it and grants each
  workload its MetaTables role (`data_viewer`, `data_writer` or `editor`, never `owner`), directly
  or through the Environment's Team (for example `alpaca-connectors-production`) for the roles
  its members share ([Table Access And MetaTables Roles](jobs.md#table-access-and-metatables-roles));
  no workload holds a role on `mainsequence.markets`: Markets registries go through the Markets
  API, which a workload's workflow grant on the `MainSequenceMarkets` branch lets it reach and
  write through ([Markets API Access](jobs.md#markets-api-access); moving an existing
  Environment: [runbook](markets-api-boundary.md)). Every workflow resource that uses MetaTables
  declares `access.branches: [{repository: MetaTables, policy: reader}]`; the `harness_agent` uses
  only the API's MCP and declares no MetaTables access
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
Job and the credential migration Job. A workload that cannot read it fails every
Alpaca call with `credential_key_unavailable`; nothing is ever written or read in plaintext.

Create the Secret once per Environment, before the first push of these workflows to it. The
platform applies a declaration only to an object that exists, so a push to an Environment without
the Secret fails as a whole:

- `alpaca` is refused with `declared_access_not_granted` (`secrets/ALPACA_CONNECTORS_DATA_KEY
  (value_reader): not_found`), and `alpaca-connectors-agent` is refused with it.
- The migration Job is not admitted, and nothing migrates or deploys except the Jobs that declare
  nothing on the Secret.

Production pushes failed this way on 2026-10-25 (`8cb0023`, workflow run
`6f083527-f74d-46ca-b9a0-046cdd6ea8f7`) and 2026-10-27 (`daeee4c`, run
`b6bfc6ca-d598-445b-b007-c7fa4beaabe6`).

The first command prints the keyring. Store its output as the Secret's value, in Command Center or
with the CLI, and never commit or log it. The CLI creates the Secret in the Environment of the
checkout's current branch, so run it from a checkout of the branch that deploys there (`main` for
Production). It prompts for the value without echoing it. Push again afterwards, so the workflow
runs with the Secret in place:

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
