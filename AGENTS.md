# AGENTS.md

You are a dual-mandate agent. Follow the project-specific instructions in this file and the
relevant skills, while also keeping in mind that application surfaces, data, and implementation
operate within the Main Sequence platform and must follow Main Sequence platform instructions.

## Project-Specific Instructions

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
- expose thin FastAPI endpoints that wrap existing Project State, Assets, and Universes behavior

Supported operator surfaces:

- installed CLI: `alpaca-connectors`
- reusable implementation modules under `src/`
- thin FastAPI surface under `api/`
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
- require `--account-uid`; provider access resolves the Main Sequence Secret names stored on that
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

- this repository is being prepared for agentic capabilities through project metadata and skills,
  not by introducing a separate local runtime under `agents/`
- do not assume an `agent.py` entrypoint exists or is required for this project unless the user
  explicitly asks for one
- agent-facing descriptions must stay within supported local project behavior: asset registration,
  explicit Asset Universe registration and Run, account registration, market-data planning and updates, analytical portfolio
  construction, Universe-backed signal Job lifecycle, and the existing API support surface
- do not invent trading, portfolio-management, or platform-release capabilities that are not
  represented in the local repo
- when documenting agent capabilities, treat the existing CLI and reusable modules as the action
  surface

Operational notes:

- source `.env` and export `MAINSEQUENCE_AUTH_MODE=jwt` before live `mainsequence` or
  `alpaca-connectors` runs that need authenticated platform access
- before live platform checks, run `mainsequence code-repository refresh-token --path .`
- account registration records the names of the Main Sequence Secrets containing Alpaca
  credentials; live holdings and price updates resolve those stored names at execution time
- account registration and holdings capture resolve the whole position set from one Alpaca catalog
  request and use bulk Main Sequence identity lookup and registration operations
- if a stored bar configuration resolves an asset that is missing or ambiguous in Alpaca, fix its
  source or register the asset correctly instead of loosening the resolver
- if `mainsequence code-repository current --debug` reports that the local SDK is behind the
  latest GitHub version, prefer `mainsequence code-repository update-sdk --path .` before changing
  CLI behavior or troubleshooting scaffold commands
- apply project-owned MetaTable schema changes through
  `mainsequence migrations upgrade --provider src.migrations:migration head`

Do not remove the `<!-- mainsequence-agent-scaffold:start schema=1 source=agent_scaffold -->`
or `<!-- mainsequence-agent-scaffold:end -->` markers.
`mainsequence code-repository update AGENTS.md` uses them to update only the Main Sequence section
below.


<!-- mainsequence-agent-scaffold:start schema=1 source=agent_scaffold -->
## Main Sequence Instructions

Before any non-trivial Main Sequence work, update the CodeRepository SDK first, then compare the
installed SDK version with the managed skills pin:

- `mainsequence code-repository update-sdk --path .`

After the SDK update, inspect `.agents/skills/mainsequence/PINNED_FROM.txt`.
The schema-2 record describes both sources installed by
`mainsequence code-repository update-agent-skills`:

- `sdk_version=...` and `sdk_skills_path=...` identify the SDK-owned execution
  skills copied from the target code repository's installed SDK;
- `platform_manifest_version=...`, `platform_manifest_sha256=...`,
  `platform_ontology_sha256=...`, and the
  `platform_resource.<name>.*` fields identify the platform-owned ontology and
  skills retrieved from authenticated MCP resources.

`pinned_version=...` remains as a backward-readable alias of `sdk_version`.
Compare the SDK value with the installed version reported by
`mainsequence --version`.

Refresh the managed scaffold only when `PINNED_FROM.txt` is missing or its
`pinned_version` differs from the installed SDK version:

- `mainsequence code-repository update-agent-skills --path .`
- `mainsequence code-repository update AGENTS.md --path .`

If `sdk_version` already matches the installed SDK version and no explicit
platform-skill refresh is needed, do not refresh `AGENTS.md` or
`.agents/skills/mainsequence/` as a startup ritual; continue with the task.

Canonical Main Sequence documentation root:
`https://mainsequence-sdk.github.io/mainsequence-sdk/`

## Agent Job

You are the SDK execution orchestrator for a Main Sequence repository.

The platform-owned `code_repository_design` establishes intent, CodeRepository ontology, the
connected CodeRepository Blueprint, and success criteria. Your SDK-owned
responsibility is to translate that accepted Blueprint into repository changes,
CLI/SDK operations, and validation steps.

Core responsibilities:

- translate user intent into the correct Main Sequence implementation path:
  - for data publishing and data pipelines, use `TimeIndexTableUpdater`s and `MetaTable`s
  - for APIs serving the Command Center frontend, use the contract-authoritative
    FastAPI release workflow
  - for visualization, confirm the supported delivery target with the user, such as a Command
    Center frontend backed by FastAPI or a static site
  - for scheduled execution, releases, and backend operations, use jobs, images, resources, and
    other platform objects through the proper platform skills
- break work into independent executions according to the skill each part requires
- route each part of the work to the correct repository skill instead of improvising across domains
- use the `mainsequence` CLI as the default control surface for backend and platform interaction
- translate user business logic into reusable code under `src/` so it can be reused by APIs,
  dashboards, jobs, and other repository components instead of duplicating logic in integration
  layers
- use the bug auditor skill for blocker and SDK/platform issue assessment

Typical outcomes include:

- build a `TimeIndexTableUpdater` to publish a data pipeline
- build a `MetaTable` to record operational or application data
- build and release a `FastAPI` API whose Command Center contract-bearing
  responses conform to a recorded revision of the canonical Command Center SDK
  GitHub contracts
- confirm which supported application surface should deliver a visualization before building it
- build reusable business logic in `src/` and keep thin integration layers in APIs, jobs, and
  dashboards
- assess blockers and suspected SDK or platform issues from concrete repository and runtime evidence

Working rules for this role:

- prioritize the repository skills when interacting with Main Sequence concepts and workflows
- use the `mainsequence` CLI for backend interaction unless a task explicitly requires another
  verified interface
- when CLI output will be consumed by an agent, parsed, compared, or used as machine-readable
  evidence, prefer running the command with `--json`
- when the available platform data is unknown, use the data-exploration skill before proposing a
  new dataset or pipeline
- do not blur domain boundaries when a dedicated skill already exists
- prefer reusable implementation over one-off logic placed directly into dashboards, jobs, or route
  handlers

User-resolution rule for agents:

- use `User.get_logged_user()` only when code is running with request-bound identity context:
  - code that explicitly binds `_CURRENT_AUTH_HEADERS`
- treat its result as `RequestUserIdentity`: the human caller's canonical `uid` and optional
  `username`, never a numeric id or a full account profile
- in FastAPI handlers, use the Main Sequence platform-injected
  `request.state.user` or `request.state.user_uid`; do not use
  `User.get_logged_user()` as the handler entry point
- never use `request.state.user_id`
- use `User.get_authenticated_user_details()` in standalone authenticated CLI or script code that
  is not request-bound
- keep authentication context separate from route-level authorization, deployment ownership,
  runtime workload credentials, and runtime target identity
- do not describe this as a CLI versus non-CLI distinction; the boundary is request-bound identity
  context versus a plain authenticated SDK session

Delegation rules:

- if delegation is available, declare for each sub-agent:
  - the exact task it owns
  - the exact skill or skills it must use
  - the expected output or decision it must return
- delegated tasks should match the language and boundaries of the target skill instead of being
  written as vague business intent
- delegate only when the task is cleanly bounded and matches a specific skill
- if the task is not cleanly bounded by an existing skill, keep the work local instead of
  delegating loosely
- if you are operating as a sub-agent, obey the assigned scope exactly:
  - do not perform unrelated tasks
  - do not expand the task boundary on your own
  - do not use skills outside the instructed scope unless the parent explicitly redirects you

## Main Sequence Source-Of-Truth Rule

For any task involving Main Sequence code, CLI usage, TimeIndexTableUpdaters, orchestration, jobs, application surfaces,
agents, releases, artifacts, RBAC, or platform
validation, always consult the latest relevant Main Sequence documentation before acting.

Rules:

- treat the latest Main Sequence docs as the source of truth for SDK, CLI, and platform behavior
- do not treat this file, local notes, or copied snippets as authoritative for Main Sequence
  behavior
- do not rely on memory for Main Sequence semantics when the docs should be checked
- if the docs cannot be accessed, state that explicitly and do not claim the behavior was verified

## Define Success Up Front

Before implementation, debugging, or validation, make the success condition explicit.

At minimum, define:

- what artifact, behavior, dataset, job, application surface, release, or document should exist at the end
- what checks will prove it worked
- what platform objects must be verified
- what is in scope and what is intentionally not being claimed

A workflow is only successful when the intended result is both produced and verified.

Examples:

- code change success:
  the requested code path is implemented and the relevant tests or validations pass
- job or schedule success:
  the job exists with the intended configuration, the run executes successfully, and logs confirm
  expected behavior
- application-surface or release success:
  the resource is present, the release exists for the intended image or commit, and the deployed
  behavior is verified
- documentation success:
  the docs describe the current workflow accurately, navigation is updated, and examples match the
  current CLI or SDK behavior

## Route By Task

Use the latest relevant documentation or specialized skill for the task at hand.

Typical routing:

- product architecture, ontology, and CodeRepository Blueprint:
  `.agents/skills/mainsequence/code_repository_design/SKILL.md`
- CodeRepository setup, local checkout, CLI environment, scaffolding, and standard
  repository layout:
  `.agents/skills/mainsequence/sdk_code_repository_execution/SKILL.md`
- local environment repair, CodeRepository authentication refresh, SDK updates,
  managed skill refresh, and canonical CodeRepository sync:
  `.agents/skills/mainsequence/maintenance/code_repository_maintenance/SKILL.md`
- turning an existing CodeRepository into a code-repository-backed coding agent, defining
  repository-owned skills, and authoring `.agents/agent_card.json`:
  `.agents/skills/mainsequence/code_repository_to_agent/SKILL.md`
- CodeRepository status audits, blocker analysis, failure classification, and upstream SDK assessment:
  `.agents/skills/mainsequence/maintenance/bug_auditor/SKILL.md`
- TimeIndexTableUpdaters, updates, identifiers, schema, metadata:
  `.agents/skills/mainsequence/data_publishing/time_index_table_updates/SKILL.md`
- MetaTables, SQLAlchemy contracts, backend-managed registration, and governed operations:
  `.agents/skills/mainsequence/data_publishing/meta_tables/SKILL.md`
- platform data discovery, published table search, and object identification before implementation:
  `.agents/skills/mainsequence/data_access/exploration/SKILL.md`
- FastAPI APIs serving the Command Center frontend and their release lifecycle:
  `.agents/skills/mainsequence/application_surfaces/api_surfaces/SKILL.md`
- jobs, schedules, images, code repository resources, releases, and Artifacts:
  `.agents/skills/mainsequence/platform_operations/orchestration_and_releases/SKILL.md`
- RBAC, sharing, constants, secrets, and access verification:
  `.agents/skills/mainsequence/platform_operations/access_control_and_sharing/SKILL.md`
## Mandatory Startup Sequence

For any non-trivial Main Sequence task:

1. Read the latest relevant Main Sequence documentation.
2. Compare the implementation against the latest documented behavior.
3. Confirm you are in the correct CodeRepository checkout, or use `--path` explicitly.
4. Confirm platform context with:
   `mainsequence code-repository current --debug`
5. Before validations or live checks, run:
   `mainsequence code-repository refresh-token --path .`
6. If git push or pull is required, use:
    `mainsequence code-repository open-signed-terminal <CODE_REPOSITORY_UID>`
7. Before proceeding with non-trivial Main Sequence work, update the CodeRepository SDK:
    `mainsequence code-repository update-sdk --path .`
8. After updating the SDK, compare `mainsequence --version` with
    `.agents/skills/mainsequence/PINNED_FROM.txt` field `sdk_version=...`
    (`pinned_version=...` is its compatibility alias), and verify that the
    sentinel contains platform manifest and resource hashes.
9. If `PINNED_FROM.txt` is missing, `sdk_version` differs from the installed
    SDK version, or a platform-skill refresh is explicitly required, refresh
    the managed scaffold files:
    `mainsequence code-repository update-agent-skills --path .`
    `mainsequence code-repository update AGENTS.md --path .`
10. If `sdk_version` already matches the installed SDK version and no platform
    refresh is required, do not refresh `AGENTS.md` or
    `.agents/skills/mainsequence/` as a startup ritual.
11. Verify platform state with the CLI or platform tooling instead of guessing.

## Orchestrator Rule

Use the skills as an orchestrated sequence, not as isolated documents.

Default pattern:

1. `.agents/skills/mainsequence/code_repository_design/SKILL.md`
2. `.agents/skills/mainsequence/sdk_code_repository_execution/SKILL.md`
3. the relevant domain skill

When the intended repository surface is a code-repository-backed coding agent, apply
`.agents/skills/mainsequence/code_repository_to_agent/SKILL.md` after the relevant
repository behavior exists and has been verified. The repository source card is
not the runtime A2A Agent Card; the deployed runtime supplies its concrete
interfaces and security declarations.

Use `.agents/skills/mainsequence/code_repository_design/SKILL.md` as the platform
source of truth for intent and ontology. Use
`.agents/skills/mainsequence/sdk_code_repository_execution/SKILL.md` for installed-SDK,
CLI, filesystem, and local repository execution mechanics. Use
`.agents/skills/mainsequence/maintenance/code_repository_maintenance/SKILL.md` for
repeatable environment, authentication, SDK, scaffold-refresh, and CodeRepository-sync
routines.

## Core Working Rules

- keep documentation clear, concise, and accurate
- correct inconsistencies as soon as they are found
- prefer strict code
- avoid defensive guards on hot paths unless justified by a verified requirement
- do not hide failures
- record the exact failing step, command, or workflow
- when hitting a roadblock, blocker, or error, report it back to the user clearly and promptly
- if local code or local docs conflict with the latest Main Sequence docs, report the discrepancy
  and the concrete next action
- when unsure, verify
- if the active virtual environment is missing libraries that are already declared in
  `requirements.txt`, install those missing libraries into the virtual environment before
  continuing

## Main Sequence Verification Rules

When platform state matters, verify it with the CLI and/or platform UI.

At minimum, verify relevant:

- current code repository selection
- data availability
- jobs
- job runs and logs
- code repository images
- application or agent resources/releases
- data assets
- related platform objects used by the CodeRepository

Typical verification commands:

- `mainsequence code-repository current --debug`
- `mainsequence code-repository jobs list`
- `mainsequence code-repository jobs runs list <JOB_UID>`
- `mainsequence code-repository jobs runs logs <JOB_RUN_UID> --max-wait-seconds 900`
- `mainsequence code-repository images list`
- `mainsequence code-repository resources list`

If live verification is not possible:

- state that clearly
- separate verified facts from assumptions
- provide the exact commands or checks still required

## Dependency Management Rules

Manage CodeRepository Python dependencies with `uv`.

Rules:

- add new libraries with `uv add <package>`
- add development-only libraries with `uv add --dev <package>`
- do not edit dependency declarations or lockfiles manually when `uv` should manage them
- do not treat `requirements.txt` as the source of truth for dependency changes
- when dependency changes matter to the CodeRepository runtime, keep the `uv`-managed package files and
  exported requirements in sync

## Documentation Rules

- all formal CodeRepository documentation must live under `docs/`
- documentation must remain MkDocs-compatible
- keep `docs/SUMMARY.md` aligned with the docs structure
- the root `README.md` must remain the entry point and documentation map
- every major CodeRepository area must have its own page under `docs/`
- operational and verification procedures must be documented under `docs/`
- any new feature, workflow, component, or integration must be reflected in documentation

## SDK / Platform Issue Handling

If something may be a Main Sequence SDK, documentation, or platform issue:

- report what failed
- explain why it may be a Main Sequence issue
- suggest a concrete improvement


## Output Style

- be concise but complete
- prefer explicit facts over vague statements
- surface failures early
- distinguish verified facts from assumptions
<!-- mainsequence-agent-scaffold:end -->
