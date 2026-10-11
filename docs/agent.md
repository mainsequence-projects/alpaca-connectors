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

- `AGENTS.md` defines the project boundary and safety rules.
- `.agents/agent_card.json` contains stable source metadata and six repository-owned skills.
- `.agents/skills/` explains the supported workflows and their invariants.
- `api/agent/main.py` is the Agent's entry point: the Tau runtime from `ms_tau_sdk.create_app()`.
- `src/alpaca_connectors/api/app/mcp_server.py` serves the structured project tools from
  `alpaca_connectors.agent_tools` at the API's `/mcp`.
- `.mainsequence/workflows/alpaca-connectors-api.yaml` declares the `alpaca` API with
  `mcp_enabled: true` and the `harness_agent` that uses it.
- `.agents/alpaca-command-center-icon.png` is the Alpaca artwork. The workflow no longer declares an
  Agent icon, because the platform dropped that field.

The platform builds the complete runtime A2A Agent Card at deployment. Concrete interfaces,
security declarations, and protocol versions are intentionally absent from the repository source
card; it declares its supported response kind and business skills.

There is no project `.tau/` override or in-process extension tool directory. TAU uses its packaged
system instructions plus repository instructions and discovered skills. The installed loader
discovers the six direct `.agents/skills/<capability>/SKILL.md` entries. The nested SDK and
platform skill bundles are development guidance, not additional automatically loaded business
skills.

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
Job workload users obtain table access through the Environment's Team. Its Writer grants cover
`mainsequence.markets` and the separate `alpaca-connectors` namespace holding this project's
Alembic version registry; the Harness needs no direct table grant to use these tools. See
[Table Access Through A Team](operations/jobs.md#table-access-through-a-team).

## Operational Boundaries

- The Organization Environment is derived from the deployed CodeRepositoryBranch and is never a
  user or tool argument.
- No tool accepts Alpaca credential values. To register an account or replace its keys from keys
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
- The 17 Alpaca tools expose no trading, order submission, arbitrary SQL, shell execution, or
  unrelated platform-release operations. Those are outside this application's supported scope.

### Restricted runtime tool scope

The Harness workflow sets both `TAU_EXCLUDE_BASE_TOOLS=true` and
`TAU_EXCLUDE_MAINSEQUENCE_MCP=true`. These SDK settings remove generic filesystem reading,
`write`, `edit`, `bash`, and the general Main Sequence MCP connection, tools, and resource prompt.
This is a runtime tool restriction, not a prompt-only instruction.

The model-facing catalog retains:

- `alpaca__list_tools` and `alpaca__call_tool`, reaching only the declared Alpaca application's
  17 project tools;
- a restricted `read` that can open discovered skill files and their supporting files, not
  arbitrary repository files or paths outside those skills;
- SDK `task_request_input` and `task_request_authorization` controls for the Agent's own A2A
  Task context. These are runtime controls, not general platform operations.

No project extension tools are installed. Runtime authentication, requester delegation, and the
Alpaca application's own platform access remain intact. The CLI examples in repository skills
are for human/developer workflows; the restricted Harness has no shell fallback. If an Alpaca
tool cannot perform a requested operation, the Agent must report that limitation.

These settings are declared on the Harness resource only; they do not affect the API, CLI, or
Jobs. A local launch must supply the same two settings explicitly. The hosted restriction takes
effect only after the changed workflow is pushed and a new Agent revision becomes active;
editing the workflow does not change an already deployed revision.

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
on workflow API `2.3.0`, next to the `alpaca` API whose MCP it uses
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
and its `command_center_icon`, `cpu_limit`, and `memory_limit` fields are rejected by the
platform, so the workflow no longer declares an icon.

The explicit execution chain builds the API's image, runs this project's migration Job from it,
and then deploys the API. The Harness is part of the same chain: `agent_image` builds its image and
`deploy_agent` needs `deploy_api` and that image, so a new Agent revision never serves before the
API whose MCP it calls, and a failed migration stops both. The Agent still accesses project data
only through the API. Verify both releases separately. The
account-key-entry link currently targets the development Command Center at `localhost:5173`;
`ALPACA_CONNECTORS_SITE_URL` must point to the intended, person-accessible site before using this
flow outside that development setup.

The two resources name each other in their `access` blocks. The Agent has `view` on the `alpaca`
release, the application it calls. The API has `view` on the Agent, so it can find the Agent that
the Alpaca site opens. Both entries name resources declared on this branch, so any push may apply
them; only `acts_for_requester` needed an Organization admin's push, and it is already on.

The project keeps Uvicorn on the `0.52` line with a non-exact `>=0.52.4,<0.53` project range;
`uv.lock` records the reproducible installed version.

Platform validation of the file succeeds only after the push that indexes `api/agent/main.py`.
Repository preparation and workflow validation do not alone prove a live Agent is ready. After a
push, verify the workflow action, exact-commit image chain, deployment run, Agent readiness, the
`alpaca__list_tools` result (the 17-tool catalog), and that the API release advertises its MCP
connection before relying on it.

## Current local compatibility review

The installed and locked versions are Main Sequence **9.0.22**, TAU **2.0.12**, MetaTables
(`mainsequence-metatable`) **0.1.34**, and ms-markets **2.2.10**. These matched the latest stable
published releases verified directly against the package registry on 2026-10-11. The latest
retry upgrades Main Sequence from 9.0.20 and TAU from 2.0.11; MetaTables and ms-markets were
already current. No unrelated dependency versions changed. `pyproject.toml` retains non-exact
compatibility ranges, while `uv.lock` and the exported `requirements.txt` record the resolution.

Verification on these versions passed all 420 tests (plus 72 subtests), Ruff lint, actual API
and Harness app imports, and TAU discovery of all six repository-owned skills without diagnostics.
The MCP catalog contains nine read-only tools and eight mutation/multi-operation tools. This
local review alone does not certify a deployed Agent or hosted migration completion.
The MetaTables SDK compatibility check and installed dependency consistency check also passed.

Tool-restriction regressions exercise the installed TAU runtime's catalog assembly with mocked
backend/model I/O: only the Alpaca pair, skill reader, and A2A Task controls remain. They also
verify that the real skill reader opens business skills and rejects application files outside
those skills.

The development skill bundles were explicitly refreshed from the installed packages: Main
Sequence 9.0.22, TAU 2.0.12, MetaTables 0.1.34, and ms-markets 2.2.10. Each bundle's
`PINNED_FROM.txt` records its matching version. The six repository-owned business skills were
preserved.

The authenticated local API smoke test initializes a real MCP session, discovers all 17 tools,
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
