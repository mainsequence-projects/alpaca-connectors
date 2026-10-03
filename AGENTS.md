# AGENTS.md

You are a dual-mandate agent. Follow the CodeRepository-specific instructions in this file and the
relevant skills, while also keeping in mind that application surfaces, data, and implementation
operate within the Main Sequence platform and must follow Main Sequence platform instructions.

## CodeRepository-Specific Instructions

This repository is the Alpaca connector project for Main Sequence.

Primary project capabilities:

- report the implemented capability catalog and current provider configuration
- register Alpaca US equity assets as Main Sequence public assets
- register reusable `AssetUniverse` resources that link explicit sources to `AssetCategory`
  materialization targets through real foreign keys
- register and refresh Alpaca brokerage accounts and their holdings snapshots
- publish Alpaca stock-market data for one asset or a reusable asset universe
- build ETF-tracking ms-markets portfolios from ETF holdings signals and interpolated Alpaca bars
- create and operate one dedicated scheduled Job per Universe-backed Alpaca ETF signal
- expose thin FastAPI endpoints for the supported project state, Asset, Account, holdings,
  Universe, bars, ETF signal, rebalance, portfolio, and JobRun operations

Supported operator surfaces:

- installed CLI: `alpaca-connectors`
- reusable implementation modules under `src/`
- thin FastAPI surface under `api/`
- structured Tau project tools under `.tau/extensions/alpaca_connectors/`
- repository-local scheduled-job entrypoints under `src/jobs/`

Repository rules:

- treat Assets, Universes, Market Data, Accounts, Holdings, Portfolios, and Operations as the
  business capability boundaries; providers are supporting dependencies, not a `Connection`
  domain
- keep API-only capability descriptions under `api/app`; `src/` is reserved for reusable backend
  behavior
- use `src.universes`, `src.market_data`, `src.account`, `src.holdings`, and `src.portfolios` as
  the public capability import paths
- keep reusable planning, registration, holdings orchestration, and stock-bar logic under `src/`;
  ETF provider extraction itself comes from the `etfhextractor` dependency; do not reintroduce
  one-off workflow scripts for the main operations
- keep CLI-facing behavior thin; CLI commands should orchestrate reusable modules, not duplicate
  business logic
- keep durable signal Job desired state and reconciliation under `src/operations/`; JobRuns are
  execution history and never own signal configuration
- if a scheduled job still needs a repository-local launcher, keep that launcher under `src/jobs/`
  and declare the reviewed schedule under `.mainsequence/workflows/`

Asset registration:

- use `alpaca-connectors asset register`
- require `--account-uid`; provider access resolves the Main Sequence Secrets referenced by that
  registered Alpaca account, and the workflow never accepts credential values or global fallback
  credentials
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

Asset Universes and holdings categories:

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

Price updates from Alpaca:

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

FastAPI surface:

- the API is intentionally thin and should call the reusable logic already implemented under `src/`
  and the external `etfhextractor` dependency through the local adapter
- keep route handlers contract-driven and avoid rebuilding the producer logic inside route bodies
- canonical routes are grouped under `/v1/project-state`, `/v1/assets`, `/v1/accounts`,
  `/v1/universe-sources`, `/v1/universes`, `/v1/market-data/bar-configurations`, and
  `/v1/market-data/datasets`, and `/v1/signal-jobs`

Project-to-agent boundary:

- this repository exposes one CodeRepository Coding Agent through `.agents/agent_card.json`, six
  repository-owned skills, and the structured tools registered by
  `.tau/extensions/alpaca_connectors/extension.py`; do not introduce a separate `agents/` runtime
- use Tau tools in deployed CodeRepository Executor or A2A sessions and use the installed
  `alpaca-connectors` CLI in shell workflows; both must reuse the same project services
- load read-only query tools in parallel and execute mutations sequentially
- never expose an Organization Environment selector; the deployed CodeRepositoryBranch resolves it
  automatically
- Tau tools, agent sessions, and the CLI never accept Alpaca credential values; their Account
  operations accept visible Main Sequence Secret names only. The HTTP account create and rotate
  endpoints are the only surfaces that accept values, solely to store them in application-managed
  Secrets (ADR 0011); never echo, log, or persist those values anywhere else
- destructive Tau operations require exact `DELETE <uid>` confirmation
- long-running bars, signal, and portfolio updates must submit their existing Main Sequence Jobs
  and return a JobRun UID; inspect them with `alpaca_get_job_run_status`
- agent-facing descriptions must stay within supported local project behavior: asset registration,
  explicit Asset Universe registration and component extraction, Account registration and holdings,
  stored market-data updates, Universe-backed ETF signal Jobs, rebalance configuration, and
  analytical ETF portfolio construction
- do not invent trading, order submission, brokerage portfolio management, arbitrary SQL or shell,
  or generic platform-release capabilities
- route specialized work through the repository-owned `alpaca-asset-registration`,
  `alpaca-account-workflow`, `alpaca-holdings-category`, `alpaca-stock-bars`,
  `alpaca-etf-weight-signals`, and `alpaca-etf-portfolios` skills

Operational notes:

- source `.env` and export `MAINSEQUENCE_AUTH_MODE=jwt` before live `mainsequence`, `metatables`,
  or `alpaca-connectors` runs that need authenticated platform access; the SDK keeps the session
  in the operating-system credential store, never in `.env`
- before live platform checks, run `mainsequence refresh-token`
- account registration references two Main Sequence Secrets by UID: `managed` Secrets the API
  created from submitted values (named `ALPACA_CONNECTORS__<Account.unique_identifier>__...` and
  deleted with the registration) or `external` Secrets selected by name (never modified); live
  holdings and price updates resolve them by UID at execution time
- registrations that predate Secret UID storage need the reviewed one-time
  `alpaca-connectors account backfill-secret-uids --execute` after the `0015` migration
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
- apply project-owned MetaTable schema changes through
  `metatables migrations upgrade --provider src.migrations:migration head`

Do not remove the `<!-- mainsequence-agent-scaffold:start schema=1 source=agent_scaffold -->`
or `<!-- mainsequence-agent-scaffold:end -->` markers.
`mainsequence code-repository update AGENTS.md` uses them to update only the Main Sequence section
below.


<!-- mainsequence-agent-scaffold:start schema=1 source=agent_scaffold -->
## Main Sequence Instructions

Use this managed section to select the correct Main Sequence skill. Detailed
procedures belong in those skills and their referenced documentation.

## Authority

- Repository-specific instructions above this block define local intent and
  repository conventions.
- The installed SDK code, CLI help, SDK-owned skills, and version-matched SDK
  documentation define client behavior.
- Installed platform-owned skills and backend-advertised schemas, templates,
  and capabilities define the current platform contract.
- The public documentation site is supplemental when its SDK version differs
  from the installed package:
  `https://mainsequence-sdk.github.io/mainsequence-sdk/`
- If the installed client and platform contract disagree, do not guess or
  update automatically. Use the bug-auditor skill to record the evidence and
  identify the owning side.

## Updates Are Explicit

Do not run any of these commands unless the user explicitly requests that
specific update:

- `mainsequence code-repository update-sdk --path .`
- `mainsequence code-repository update-agent-skills --path .`
- `mainsequence code-repository update-platform-skills --path .`
- `mainsequence code-repository update AGENTS.md --path .`
- `uv run ms-tau skills sync --path .`

A missing or mismatched `.agents/skills/mainsequence/PINNED_FROM.txt` is state
to report, not permission to mutate the repository. The same rule applies to
`.agents/skills/ms_tau_sdk/PINNED_FROM.txt`. When a Main Sequence update is
requested, use the `code_repository_maintenance` skill. The `mainsequence` CLI
never owns or updates the `ms_tau_sdk` namespace.

The SDK owns all of `.agents/skills/mainsequence/`. Its skill copy is local,
requires no sign-in, and deletes everything absent from the installed SDK
bundle. Platform skills are installed separately under
`.agents/skills/mainsequence_platform/` by the authenticated platform update.

## Route By Task

- Product architecture, platform ontology, and CodeRepository Blueprint work:
  use the matching platform-owned design skill declared by the installed
  platform catalog. Do not assume its filesystem path.
- CodeRepository context, local SDK execution, repository structure, and
  implementation routing:
  `.agents/skills/mainsequence/sdk_code_repository_execution/SKILL.md`
- Local environment repair, authentication, explicitly requested updates, and
  canonical CodeRepository sync:
  `.agents/skills/mainsequence/maintenance/code_repository_maintenance/SKILL.md`
- Blocker analysis, failure classification, and SDK/platform contract mismatches:
  `.agents/skills/mainsequence/maintenance/bug_auditor/SKILL.md`
- MetaTable modeling, queries, external registration, table updates, Alembic
  schema changes, and data discovery: use the skills the `metatables` package
  installs under `.agents/skills/metatables/` and its documentation.
- Code that still imports `mainsequence.meta_tables` or
  `mainsequence.client.metatables`, or calls the retired `mainsequence` table
  and migration commands:
  `.agents/skills/mainsequence/maintenance/metatables_transition/SKILL.md`
- FastAPI APIs serving the Command Center frontend:
  `.agents/skills/mainsequence/application_surfaces/api_surfaces/SKILL.md`
- Jobs, schedules, images, resources, releases, and Artifacts:
  `.agents/skills/mainsequence/platform_operations/orchestration_and_releases/SKILL.md`
- RBAC, sharing, constants, secrets, and access verification:
  `.agents/skills/mainsequence/platform_operations/access_control_and_sharing/SKILL.md`
- A2A session discovery, messages, files, and SDK response handling:
  `.agents/skills/mainsequence/a2a_sdk_execution/SKILL.md`
- Turning a CodeRepository into a platform coding agent or selecting other
  platform-owned capabilities: use the matching skill declared by the installed
  platform catalog. Do not hardcode a platform-owned skill path.
- Implementing or debugging a TAU-based Harness Agent: after the platform skill
  establishes the platform contract, use the version-matched skills under
  `.agents/skills/ms_tau_sdk/`. If they are absent and the user requested the
  update, run `uv run ms-tau skills sync --path .`; do not reconstruct those
  SDK instructions from platform documentation.

## Core Working Rules

- Read the relevant skill before acting on a Main Sequence domain.
- Use the current Git checkout as repository, branch, and commit identity. Do
  not ask users to inject CodeRepositoryBranch or Environment identity.
- Use the `mainsequence` CLI as the default platform control surface. Prefer
  `--json` when output will be parsed or used as evidence.
- Use the owning domain package before designing against an unfamiliar domain contract.
- Keep reusable business logic under `src/` and keep API, job, and other
  integration layers thin.
- Verify only the platform objects relevant to the requested outcome.
- Separate verified facts from assumptions and report blockers with the exact
  failing operation.

## Completion

Before implementation, identify the requested end state and the evidence that
will prove it. Do not claim completion until the relevant code or documentation
checks pass and any required platform state has been verified. If live
verification is unavailable, state what remains unverified.
<!-- mainsequence-agent-scaffold:end -->
