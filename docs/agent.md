# Alpaca Agent

The repository exposes its existing operational capabilities as one Main Sequence Harness Agent.
The agent is an additional project surface over the same reusable services used by the CLI and
FastAPI application; it is not a second API and it does not duplicate connector logic. Its project
tools are served by the API's MCP endpoint with verified caller and requester identity
([ADR 0015](adrs/0015_agent_tools_on_the_api_mcp_and_site_key_entry.md)).

## Architecture

```text
Person → Alpaca Agent (TAU Harness, A2A)
                 │
                 └→ alpaca__list_tools / alpaca__call_tool
                                  │
                         Alpaca API /mcp
                                  │
                         shared project services ← REST / CLI
                                  │
                 ┌────────────────┼─────────────────┐
              Alpaca       ms-markets / MetaTables   workflow Jobs
                                                     │
                                              JobRun → services
```

The Agent interprets the request and selects a documented operation; it does not own connector
state or implement extraction, registration, pricing, or portfolio calculations. The reusable
Python services own that behavior. MetaTables hold the durable business configurations and
observations. Workflow Jobs own schedules and compute; JobRuns record executions, not configuration.
TAU owns the conversation, model invocation, A2A transport, and application-MCP connection.

The source card is named **Alpaca Agent**. Its six skills cover Asset registration, Accounts and
holdings, ETF Universes, stock bars, ETF weight signals, and analytical portfolios (including
rebalance configurations). The configured model is `openai-codex / gpt-5.6-luna` with `medium`
thinking, selected explicitly from the development backend's authenticated catalog.
The source card advertises completed A2A messages; a returned JobRun UID is not an A2A Task.

## Repository Artifacts

- `.tau/SYSTEM.md` is the Agent's runtime prompt: its role, its Alpaca tools and skills, identity
  and key-entry rules, confirmations, uncertain writes and refusals.
- `AGENTS.md` holds the short developer rules and the project boundary. TAU adds it to every
  session too, so the CLI and operational rules live in the
  [operator guide](operations/operator-guide.md).
- `.agents/agent_card.json` contains stable source metadata and six repository-owned skills.
- `.agents/skills/mcp_tools/` holds the six MCP-usage skills, with one authoritative copy each.
  The sibling SDK/platform skill bundles remain private development guidance.
- `api/agent/main.py` is the Agent's entry point: the Tau runtime from `ms_tau_sdk.create_app()`.
- `src/alpaca_connectors/api/app/mcp_server.py` serves the structured project tools from
  `alpaca_connectors.agent_tools` at the API's `/mcp`.
- `src/alpaca_connectors/api/app/site_agent.py` finds this Agent when a request arrives, so the
  project configuration can return it to the Alpaca site.
- `.mainsequence/workflows/alpaca-connectors-api.yaml` declares the `alpaca` API with
  `mcp_enabled: true` and the `harness_agent` that uses it.
- `.agents/alpaca-command-center-icon.png` is the Alpaca artwork. The workflow no longer declares an
  Agent icon, because the platform dropped that field.

The platform builds the complete runtime A2A Agent Card at deployment. Concrete interfaces,
security declarations, and protocol versions are intentionally absent from the repository source
card; it declares its supported response kind and business skills.

`.tau/SYSTEM.md` replaces TAU's packaged general-purpose prompt with the Alpaca Agent's runtime
instructions, and TAU still adds `AGENTS.md` to every session as project context. There is no
in-process extension tool directory, and no `.tau/AGENTS.md` or `.agents/AGENTS.md`. The business skills now
live under `.agents/skills/mcp_tools/<skill-name>/SKILL.md`, and the Agent Card points to those
same files. TAU 2.0.14 discovers and reads them through the application's authenticated MCP:
`alpaca__list_resources` lists the six skills, and `alpaca__read_resource` reads a listed file
after verifying its size and SHA-256 digest. Published skills remain application-owned; they
do not become local TAU skills. The local loader still scans immediate skill folders, but that is
not the consumption path for this Harness. No custom loader, symlink, duplicate tree or generic-
tool fallback is needed.

## Tool Surface

The Agent lists the tools with `alpaca__list_tools` and calls one with `alpaca__call_tool`, naming
it in `tool` and passing its `arguments`. Read tools for project state and supported resources:

- `alpaca_project_state`
- `alpaca_query_assets`
- `alpaca_query_accounts`
- `alpaca_query_universes`
- `alpaca_query_bar_configurations`
- `alpaca_query_etf_signals`
- `alpaca_query_rebalance_configurations`
- `alpaca_query_etf_portfolios`
- `alpaca_get_job_run_status`

Sequential mutation tools for the same application workflows:

- `alpaca_register_assets`
- `alpaca_manage_account`
- `alpaca_account_key_request`
- `alpaca_manage_universe`
- `alpaca_manage_bar_configuration`
- `alpaca_manage_etf_signal`
- `alpaca_manage_rebalance_configuration`
- `alpaca_manage_etf_portfolio`

Each multi-operation tool uses an explicit operation discriminator and a strict JSON Schema. The
MCP server runs the existing application services inside the API's request identity: authorship
and ownership follow the person the Agent serves. Arguments are validated against each schema, and
errors never quote them.

### Identity and authorization

There are three distinct identities in the execution path:

| Boundary | Identity and authorization |
| --- | --- |
| Agent calls the API | TAU supplies the platform-verified Agent caller and requester, under `acts_for_requester`. The platform checks application access. |
| API records ownership | `identity.acting_user_uid()` selects the verified requester, otherwise the caller. Key requests and operation ownership use that UID, never a UID supplied in arguments. |
| API or Job accesses backing services | Ordinary downstream SDK and MetaTables calls use that process's workload principal and grants. They do not automatically inherit the incoming requester's credentials. |

The API verifies request identity before installing MCP; its worker-thread dispatch preserves that
context. This is not a blanket claim of per-person filtering on every table. The application's
release-access boundary, explicit ownership checks, and workload permissions all matter. API and
Job workload users hold MetaTables roles on this project's `alpaca-connectors` namespace only,
granted by its owner, and reach Markets registries through the Markets API (ms-markets ADR 0047)
under the Markets registry write policy; the Harness needs no table role to use these tools. See
[Table Access And MetaTables Roles](operations/jobs.md#table-access-and-metatables-roles).

## Operational Boundaries

- The Organization Environment is derived from the deployed CodeRepositoryBranch and is never a
  user or tool argument.
- No tool accepts Alpaca credential values, and no tool result carries one: the keys are stored
  encrypted on the account row and opened only when the API calls Alpaca
  ([ADR 0017](adrs/0017_encrypted_account_credentials.md)). To register an account or replace its keys from keys
  the person holds, `alpaca_account_key_request` creates a pending request for that person and
  returns the Alpaca site link; the person types the keys on the site, which sends them to the API
  with the request UID, and the Agent reads the result with operation `status`. Registration from
  existing Main Sequence Secret names stays available through `alpaca_manage_account`.
- Account creation preserves the mandatory initial Asset registration and holdings capture.
- Universe extraction registers missing constituents in bulk, replaces the linked AssetCategory in
  bulk, and publishes one complete observed weight frame.
- Bars, ETF signal, and ETF portfolio updates submit their existing Main Sequence Jobs and return a
  JobRun UID. Large updates do not execute inline in the agent session.
- Destructive tool operations require exact `DELETE <uid>` confirmation.
- The 17 Alpaca tools expose no order submission, arbitrary SQL, shell execution, or unrelated
  platform-release operations. Order execution exists on the API for authorised applications
  ([ADR 0016](adrs/0016_order_execution_for_authorised_applications.md)); no Agent tool reaches it.

### Restricted runtime tool scope

The Harness workflow sets both `TAU_EXCLUDE_BASE_TOOLS=true` and
`TAU_EXCLUDE_MAINSEQUENCE_MCP=true`. These SDK settings remove generic filesystem reading,
`write`, `edit`, `bash`, and the general Main Sequence MCP connection, tools, and resource prompt.
This is a runtime tool restriction, not a prompt-only instruction.

The model-facing catalog retains:

- `alpaca__list_tools` and `alpaca__call_tool`, reaching only the declared Alpaca application's
  17 project tools;
- `alpaca__list_resources` and `alpaca__read_resource`, reaching only the skills/resources that
  same application advertises and verifying listed skill-file digests before returning content;
- a restricted `read` that can open discovered skill files and their supporting files, not
  arbitrary repository files or paths outside those skills. Since TAU 2.0.21 the runtime discovers
  every `SKILL.md` under `.agents/skills/` at any depth, so that index, which the prompt also
  lists, covers the six business skills and the copied Main Sequence, platform, MetaTables,
  ms-markets and TAU development bundles;
- SDK `task_request_input` and `task_request_authorization` controls for the Agent's own A2A
  Task context. These are runtime controls, not general platform operations.

No project extension tools are installed. Runtime authentication, requester delegation, and the
Alpaca application's own platform access remain intact. The six business skills describe MCP
operations only; human/developer CLI guidance stays in the repository documentation. The
restricted Harness has no shell fallback. If an Alpaca tool cannot perform a requested operation,
the Agent must report that limitation.

These settings are declared on the Harness resource only; they do not affect the API, CLI, or
Jobs. A local launch must supply the same two settings explicitly. The hosted restriction takes
effect only after the changed workflow is pushed and a new Agent revision becomes active;
editing the workflow does not change an already deployed revision.

### Single-copy MCP skills — 2026-10-13

The six repository-owned skills cover the existing application tools:

| Skill | Tool workflows covered |
| --- | --- |
| `alpaca-asset-registration` | Asset queries and exact-symbol registration planning/execution. |
| `alpaca-account-workflow` | Account and holdings queries, account mutations, and site-based key requests. |
| `alpaca-holdings-category` | Source and Universe CRUD, extraction previews, component extraction, and category members. |
| `alpaca-stock-bars` | Stored bars configuration CRUD, resolution, dataset observations, and Job submission/status. |
| `alpaca-etf-weight-signals` | Signal configuration CRUD, scheduled inclusion, Job submission/status, observations, and runs. |
| `alpaca-etf-portfolios` | Rebalance and portfolio configuration CRUD, Job submission/status, performance, statistics, and runs. |

They preserve credential, identity, bulk-operation, observed-weight, calendar and destructive-
confirmation boundaries. Each contains concrete `alpaca__call_tool` examples checked against the
actual tool schemas, query-operation guidance, bounded pagination and the shared
`alpaca_project_state` preflight. Portfolio guidance explains staleness, missing-price policy and
fractional commission units. CLI examples are removed from these Agent-facing skills.

Main Sequence 9.0.23 supports publishing a selected directory through
`mainsequence.server.mcp_skills.skills_extension`. The API configures it with the user's chosen
`.agents/skills/mcp_tools` directory. MCP clients use `skills/list`,
`skills/get`, and `resources/read`; the SDK owns the manifests and `skill://` resources.
The modern `server/discover` capability advertises `io.modelcontextprotocol/skills`; legacy
initialization remains supported. Skill requests use the same request-identity protection as
tools. Local contract tests check all six manifests and exact source bytes/digests, reject private
SDK/platform resource URIs, require authentication and verify working-directory-independent
resolution. This local implementation does not establish that a deployed revision serves it.

The original six folders are moved, not copied. Their new folder names match their lowercase,
hyphenated frontmatter names. Neither the complete `.agents/skills` tree nor managed Main Sequence,
MetaTables, ms-markets, TAU or platform development guidance is published.

Serving skills is distinct from consuming them. TAU 2.0.14 fixes application skill consumption
through `alpaca__list_resources` and `alpaca__read_resource`
([released change](https://github.com/mainsequence-sdk/ms-tau-sdk/blob/v2.0.14/CHANGELOG.md),
[TAU #98](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/98)). It first negotiates modern
MCP discovery, where the Skills extension is advertised; older applications can still initialize
with the legacy protocol. These operations use the current call's application access, not a shared
cross-person catalog. Only advertised resources or listed skill files are read.

The earlier grouped-local-discovery issue [TAU #99](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/99)
is closed. The solution used here is MCP consumption of the single nested directory. Tests now
exercise the real SDK application connector and resource tools over local HTTP: all six files must
be returned byte-for-byte with verified digests, private/traversal URIs must be refused, and
altered content must never reach the model. This does not certify a deployed revision or a live
model conversation.

TAU 2.0.21 added recursive local loading: a folder without a `SKILL.md` groups skills, and the
managed `.agents/skills/ms_tau_sdk/` skills are read too (its `tau_project_customization` skill).
The Agent therefore also reads the six business skills and the copied development bundles
locally, through its restricted `read`. A local-reader test pins that index to the repository's
`SKILL.md` files and checks that every other file, such as `pyproject.toml`, `AGENTS.md` or
`.tau/SYSTEM.md`, is still refused.

### Application-tool timeouts

TAU 2.0.11 honors a tool's MCP `_meta["mainsequence.ai/timeout-seconds/v1"]` value. An undeclared
limit uses `TAU_MCP_TOOL_TIMEOUT_SECONDS`, which defaults to **60 seconds**, and no call may outlive
its turn. The current Alpaca MCP catalog declares no per-tool limit and the workflow declares no
override, so its calls use that default.

Bars, signal, and portfolio update tools submit Jobs and return their JobRun UID for status checks.
Some other mutations, including Asset registration, holdings capture, and Universe extraction,
still execute synchronously in the API and can exceed the tool deadline. A timeout is not proof
that a write failed or was rolled back: inspect operation status or the affected resource before
retrying a mutation. The new timeout support does not automatically move these operations to Jobs.

## Deployment

`.mainsequence/workflows/alpaca-connectors-api.yaml` declares the branch's one `harness_agent`
on workflow API `3.0.0`, next to the `alpaca` API whose MCP it uses
(`mcp_applications: [{resource: alpaca}]`). Same-file declarations establish that application
reference; they do not themselves prove that either service is ready. Its `source_path`,
`api/agent/main.py`, serves
the object
`ms_tau_sdk.create_app()` returns; the platform runs it from the ordinary CodeRepository image, so
`ms-tau-sdk` is a runtime dependency in `pyproject.toml` and the exported `requirements.txt`. The
workflow enables automatic deployment and accepts every pushed commit through
`automatic_redeployment.tag_regex: null`; it contains no image, branch, Environment, or UID.

The resource declares `acts_for_requester: true`. Only an Organization admin's push enables it;
the Agent then calls the platform with the ordinary permissions of the person whose request it
serves, for at most 24 hours after that request. The retired `code_repository_coding_agent` kind
and its `cpu_limit` and `memory_limit` fields are rejected by the platform. The `harness_agent`
declares its Command Center icon and poses (`command_center_icon`, `command_center_animations`)
from the animated loops in `assets/agents/sequence/assets/alpaca/neutral/`, whose source and
rebuild steps are in `assets/agents/sequence/README.md`; the platform refuses an icon file over
128 x 128 px or 64 KiB and then keeps its default robot without failing the deployment.

The explicit execution chain builds the API's image, runs this project's migration Job from it,
and then deploys the API. The Harness is part of the same chain: `agent_image` builds its image and
`deploy_agent` needs `deploy_api` and that image, so a new Agent revision never serves before the
API whose MCP it calls, and a failed migration stops both. The Agent still accesses project data
only through the API. Verify both releases separately. The
account-key-entry link currently targets the development Command Center at `localhost:5173`;
`ALPACA_CONNECTORS_SITE_URL` must point to the intended, person-accessible site before using this
flow outside that development setup.

The two resources name each other in their `access` blocks with the smallest policy each needs.
The Agent has `user` on the `alpaca` release (see it and call it): its own calls to the API's MCP;
a call it makes for a person uses that person's access. The API has `user` on the Agent (see it
and use it), so it can find the Agent that the Alpaca site opens. Under workflow API 2.3.0 both
entries were `view`, which the platform converted to `viewer`; the 3.0.0 file lowers them. Both
entries name resources declared on this branch, so any push may apply them; only
`acts_for_requester` needed an Organization admin's push, and it is already on.

The project configuration (`GET /v1/project-state/configuration`) uses that access. It finds the
Agent when the request arrives and returns `agent_uid` with `organization_environment_uid`. The
Alpaca site mounts its conversation from that answer, and neither UID is part of the site's build.
A re-created Agent has a new UID: the push that re-creates it also re-applies the API's access, and
the API's next answer names the new Agent, without a site rebuild. See
[the API](api.md#project-state-and-the-sites-agent).

The project keeps Uvicorn on the `0.52` line with a non-exact `>=0.52.4,<0.53` project range;
`uv.lock` records the reproducible installed version.

Platform validation of the file succeeds only after the push that indexes `api/agent/main.py`.
Repository preparation and workflow validation do not alone prove a live Agent is ready. After a
push, verify the workflow action, exact-commit image chain, deployment run, Agent readiness, the
`alpaca__list_tools` result (the 17-tool catalog), and that the API release advertises its MCP
connection before relying on it.

## Current local compatibility review

The installed and locked versions are Main Sequence **9.0.30**, TAU **2.0.23**, MetaTables
(`mainsequence-metatable`) **0.1.56**, and ms-markets **2.2.10** (2026-10-25).

- Since 2026-10-15 the platform's caller assertions carry only the caller's identity (`sub` and
  `is_organization_admin`, no `team_uids`). The hosted API on Main Sequence 9.0.22 answered every
  signed call with 401, and the Agent on TAU 2.0.12 refused every caller. Main Sequence 9.0.27
  and TAU 2.0.17 dropped `team_uids` from their verifiers.
- MetaTables 0.1.51 is the first client that works with Main Sequence 9.0.30, so the two move
  together.
- TAU 2.0.22 makes a Task act for the person who asked for it and return its answer, and 2.0.23
  keeps the last batch of a Task's answer.
- MetaTables 0.1.55 ships application-encrypted columns (MetaTables ADR 0025); on 2026-10-25
  the range moved to `>=0.1.55` and resolved to 0.1.56 for
  [ADR 0017](adrs/0017_encrypted_account_credentials.md), which stores Alpaca account keys in
  such columns.

`pyproject.toml` keeps non-exact compatibility ranges (`mainsequence[mcp]>=9.0.30,<10`,
`mainsequence-metatable>=0.1.55,<0.2`, `ms-tau-sdk>=2.0.23,<3`), while `uv.lock` and the exported
`requirements.txt` record the resolution. ms-markets was not upgraded. On 2026-10-14 Main Sequence
had moved from 9.0.23 to 9.0.24, which stopped scaffolding `AGENTS.md` and ships its guidance in
the SDK skills, and MetaTables from 0.1.34 to 0.1.35. The earlier SDK upgrade moved the official
MCP dependency from **1.30.0** to **2.3.0**, with its required transport dependencies.
The `etfhextractor` Git source is pinned to release tag `v0.4.4` (commit `062f3f8`), which requires
compatible versions. No Alpaca workload reads or writes its only table, `DemoBarsTS`, so the
migration Job does not apply its provider.

The API now uses MCP 2.x `MCPServer` and its supported stateless JSON ASGI application and
session-manager lifespan, rather than the removed MCP 1.x low-level decorators. The project
catalog remains authoritative for its schemas; operation-discriminated schemas explicitly
declare their root object type. Tool names, operations, verified worker-thread identity, and
secret-safe errors are preserved. The MCP regression tests cover authenticated initialization,
the complete catalog, delegated calls, and input/error redaction.

Verification with Main Sequence 9.0.30, MetaTables 0.1.51 and TAU 2.0.23 passes all
**478 tests plus 101 subtests**, application lint/formatting,
the strict documentation build and installed dependency checks. The new real HTTP tests verify
application-MCP skill discovery and digest-checked reads, private/traversal denial, altered-content
rejection and caller isolation. The old local-index expectation is replaced with this released
application-skill contract, not a copy or custom loader.
The MCP catalog contains nine read-only tools and eight mutation/multi-operation tools. This
local review alone does not certify a deployed Agent or hosted migration completion.
The MetaTables SDK compatibility check and installed dependency consistency check also passed.

Tool-restriction regressions exercise the installed TAU runtime's catalog assembly with mocked
backend/model I/O: only the four Alpaca application tools, restricted local reader, and A2A Task
controls remain. The local reader indexes only the repository's `SKILL.md` files. Local HTTP tests
additionally verify skill consumption through MCP, refusal of private files and digest
mismatches, and separate caller identities on consecutive calls. Hosted caller tests sign
identity-only assertions, and an assertion or requester statement that lists `team_uids` is
refused with 401.

The requested development skill bundles were explicitly refreshed from the installed packages on
2026-10-21: Main Sequence 9.0.30, TAU 2.0.23 and MetaTables 0.1.51, and the platform skills from
the development backend's catalog, which no longer ships `code_repository_design`. The existing
ms-markets 2.2.10 bundle was left unchanged. Each bundle's `PINNED_FROM.txt` records its matching version. The six
repository-owned business skills remain single-copy. On 2026-10-14 `AGENTS.md` lost the retired SDK
block and was cut to its developer rules, the operator manual moved to the
[operator guide](operations/operator-guide.md), and `.tau/SYSTEM.md` became the runtime prompt.
These changes are recorded on `main`;
this local verification did not change hosted schema or certify a deployment.

The earlier 2026-10-11 authenticated local API smoke test initializes a real MCP session,
discovers all 17 tools,
and successfully reads project state, Accounts, and Universes against the migrated local
MetaTables runtime. Invalid tool arguments produce a structured error without echoing their
values. This verifies the API-to-MetaTables path, not the deployed Agent.

A separate real local TAU conversation with `openai-codex / gpt-5.6-luna` passed both an initial
Message and a follow-up using the returned context ID; the follow-up recalled the test word and
the public conversation history contained all four messages. `/health`, `/ready`, and `/version`
returned HTTP 200, with generic base and platform tools excluded. This unregistered local
bootstrap does not resolve workflow `mcp_applications`, so these checks do not certify a model
calling the Alpaca API. That integration must be verified on the deployed Harness.

The pre-publication live check found that the development backend no longer advertises the
former `openai / gpt-5.4` selection. The user selected `openai-codex / gpt-5.6-luna`, which the
current catalog supports with `medium` thinking. Publishing the already-committed source indexed
`api/agent/main.py` at commit `81388e5`; the complete workflow then passed the development
backend's API 2.3.0 validation with no errors or warnings. No resource UID was supplied to bypass
source validation. No live Alpaca Agent was found before the new deployment, and deployed
communication was unverified at that pre-publication checkpoint. The later hosted evidence and
remaining limitations are recorded below.

### Earlier hosted verification and upstream defects — 2026-10-11

The following records the earlier failures, not the current retry status. All three linked
upstream issues have since been closed. Canonical MCP repository, branch-with-workflow, and
deployment-with-workflow reads now pass. The subsequent `8b5deaf` workflow completed successfully,
including its migration Job and API deployment. SDK 9.0.22 also passes the isolated startup-state
read-timeout regression: one bounded request returns an empty logger enrichment instead of
aborting startup, without echoing the exception's credential-sensitive text.

The updated platform contract deliberately limits a hosted direct **Message** to 90 seconds
from receipt by the target. TAU 2.0.12 stops an over-limit turn and returns a structured
`a2a_message_limit_exceeded` error; increasing a client timeout does not extend this limit.
Use Task mode for longer work only when fresh Agent discovery advertises Task support. This
Agent currently advertises Message only, so test short read operations separately and submit
long bars, signal, and portfolio computations through the existing Job tools. Never replay a
Message after an ambiguous transport outcome. Local TAU has no platform Message limit, so a
local success alone does not establish hosted timing compatibility.

After publishing an SDK update, inspect the new exact-commit workflow and both active release
revisions before testing the deployed API and Agent; an earlier successful revision is not proof
of the new dependency image. Preserve the migration gate and Team grants during this verification.

The Environment's Team access was repaired without direct workload grants. The existing migration
Job applied revisions `0015`–`0017`, and the subsequent exact-commit image → migration → API workflow
succeeded. Agent discovery reported **Alpaca Agent**, a current Harness runtime, the requested Luna
model, and the declared Alpaca MCP application. The Harness was not added to the table-writer Team.

The deployed Agent discovered all 17 project tools and successfully read project state, Accounts,
and Universes. A distinct same-session follow-up returned the remembered results without repeating
those operations. This proves deployed application-tool access and conversation continuity, not
failure-free direct-Message delivery.

Two fresh Message tests returned `a2a_transport_outcome_ambiguous` even though the runtime later
completed successfully. The narrower test used two read-only tool calls; owner-scoped events
recorded a successful 110-second turn, while the backend's canonical sender has a 90-second HTTP
read cap. This independent sender deadline is tracked in
[backend issue #936](https://github.com/Main-Sequence-Server-Side/tdag-django/issues/936).
TAU's earlier per-call HTTP deadline defect is already fixed in the installed 2.0.11 release; it is
not the same boundary. Do not replay an ambiguous Message or silently switch it to Task: this
Agent advertises Message only.

Canonical MCP repository and workflow-including deployment reads also reject otherwise valid
REST results because their output schemas omit discovery metadata and workflow-step errors.
[Backend issue #937](https://github.com/Main-Sequence-Server-Side/tdag-django/issues/937) records the
reproduction and the exact rejected fields. These read failures are not evidence of a failed
deployment. Both issues were filed after duplicate searches; no backend or TAU source was changed.

The repository-owned unknown-tool error response has been hardened to omit client-supplied tool
names, with HTTP-boundary regression coverage. This preserves the existing error code and does not
change the supported tool catalog or introduce a retry mechanism.

The follow-up candidate at `21a7f6c` built successfully and its Harness deployment succeeded.
A fresh, short Message to the deployed Luna Agent returned the requested test marker in 32
seconds. It intentionally called no tools, so it verifies basic Message delivery on the new
Harness, not successful long application-tool turns or the resolution of issue #936.

That candidate's API rollout did not complete: migration JobRun
`daaf2e1f-98ae-4587-85c9-649b4b8b6779` failed with a 10-second backend HTTP read timeout.
The migration gate correctly blocked the API; the prior ready API revision continued serving.
At that checkpoint the corrected unknown-tool response was verified locally but had not yet been
verified on the hosted API.

A separate network-free regression reproduces an installed Main Sequence SDK 9.0.20 defect:
logger bootstrap uses a raw 10-second Job startup-state GET, outside the ordinary SDK transport
budget, and propagates a read timeout before building the logger. This is tracked in
[SDK issue #215](https://github.com/mainsequence-sdk/mainsequence-sdk/issues/215). The bounded
Job logs do not identify the failed HTTP route, so the isolated regression establishes the SDK
defect without claiming that the live call site is proven. No client monkey patch, authentication
fallback, hosted developer migration, or migration-gate bypass was added.
