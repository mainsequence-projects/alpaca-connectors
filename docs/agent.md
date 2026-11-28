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

The source card is named **Alpaca Agent**. Its four skills cover Asset registration, Accounts and
holdings, stock bars, and portfolios built from provided weights (including rebalance
configurations). The configured model is `openai-codex / gpt-5.6-luna` with `medium`
thinking, selected explicitly from the development backend's authenticated catalog.
The source card advertises completed A2A messages; a returned JobRun UID is not an A2A Task.

## Repository Artifacts

- `.tau/SYSTEM.md` is the Agent's runtime prompt: its role, its Alpaca tools and skills, identity
  and key-entry rules, confirmations, uncertain writes and refusals.
- `AGENTS.md` holds the short developer rules and the project boundary. It is for developers:
  the deployed Agent reads only `.tau/`. The CLI and operational rules live in the
  [operator guide](operations/operator-guide.md).
- `.agents/agent_card.json` contains stable source metadata and four repository-owned skills.
- `.tau/skills/` holds the four MCP-usage skills directly under it, with one authoritative copy
  each: the Agent's own skills and the skills the API publishes. The SDK/platform skill bundles
  under `.agents/skills/` remain private development guidance that the Agent does not read.
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
instructions. Since TAU 2.0.26
([ADR 0025](https://github.com/mainsequence-sdk/ms-tau-sdk/blob/v2.0.26/docs/adrs/0025-runtime-resources-only-from-tau.md))
the deployed Agent reads its resources only from `.tau/`: the root `AGENTS.md` and
`.agents/skills/` no longer reach it, and the workflow does not admit those development
resources. There is no in-process extension tool directory and no `.tau/AGENTS.md`, so the Agent
has no project context file. The business skills live under
`.tau/skills/<skill-name>/SKILL.md`, and the Agent Card points to those same files. TAU 2.0.14
discovers and reads them through the application's authenticated MCP: `alpaca__list_resources`
lists the four skills, and `alpaca__read_resource` reads a listed file after verifying its size and
SHA-256 digest. The same four are the Agent's local TAU skills, but the MCP is the consumption path
`.tau/SYSTEM.md` names. No custom loader, symlink, duplicate tree or generic-tool fallback is
needed.

## Tool Surface

The Agent lists the tools with `alpaca__list_tools` and calls one with `alpaca__call_tool`, naming
it in `tool` and passing its `arguments`. Read tools for project state and supported resources:

- `alpaca_project_state`
- `alpaca_query_assets`
- `alpaca_query_accounts`
- `alpaca_query_bar_configurations`
- `alpaca_query_rebalance_configurations`
- `alpaca_query_portfolios`
- `alpaca_get_job_run_status`

Sequential mutation tools for the same application workflows:

- `alpaca_register_assets`
- `alpaca_manage_account`
- `alpaca_account_key_request`
- `alpaca_manage_bar_configuration`
- `alpaca_manage_rebalance_configuration`
- `alpaca_manage_portfolio`

`alpaca_manage_portfolio` runs a portfolio, and pauses, resumes or deletes one for its owner. No
tool creates a portfolio or a signal definition or gives weights: the account's owner does that
through the application over HTTP ([ADR 0024](adrs/0024_portfolios_from_provided_weights.md),
[ADR 0027](adrs/0027_one_portfolio_path.md),
[ADR 0028](adrs/0028_every_portfolio_belongs_to_one_account.md)).

`alpaca_manage_account` removes an account's keys (`remove_keys`), or reports and runs its
deletion (`plan_delete`, then `delete` with the exact `DELETE <account_uid>` confirmation), for the
account's owner only. Removing keys keeps everything else; deleting deletes the account and all it
owns.

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
granted by its owner, and read and write Markets registries through the Markets API (ms-markets
ADR 0047), which their workflow grant on the `MainSequenceMarkets` branch lets them reach; the
Harness needs no table role to use these tools. See
[Table Access And MetaTables Roles](operations/jobs.md#table-access-and-metatables-roles).

Every Alpaca tool call also carries what the Agent was given and which session it serves. Since
TAU 2.0.29 the runtime passes the caller's W3C `traceparent` and `baggage` on to every
application call, and since 2.0.30 it names the turn's session in
`X-MainSequence-Agent-Session-UID`. The API reads neither as identity:

- A requester-bound call acts for its requester and ignores the caller context
  ([ADR 0019](adrs/0019_account_owners_vouched_for_by_trusted_applications.md)), so a person of a
  gateway application cannot be named through the Agent. A call the Agent makes as itself acts
  for the Agent, which is not on the vouching allow-list.
  `tests/test_account_ownership.py::test_a_requester_bound_call_ignores_the_context` covers the
  first case.
- No route reads the session header.
- Since TAU 3.0.4 the Agent's request, turn and each tool call are spans of the caller's trace, so
  the API's log lines for a tool call share the Agent's trace id (Main Sequence ADR 0042).

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
- Bars and portfolio updates submit their existing Main Sequence Jobs and return a JobRun UID.
  Large updates do not execute inline in the agent session.
- Destructive tool operations require exact `DELETE <uid>` confirmation. Deleting a portfolio
  retires its `Portfolio` first, and is refused while an account's target names it.
- The 13 Alpaca tools expose no order submission, arbitrary SQL, shell execution, or unrelated
  platform-release operations. Order execution exists on the API for authorised applications
  ([ADR 0016](adrs/0016_order_execution_for_authorised_applications.md)); no Agent tool reaches it.

### Restricted runtime tool scope

The Harness workflow sets both `TAU_EXCLUDE_BASE_TOOLS=true` and
`TAU_EXCLUDE_MAINSEQUENCE_MCP=true`. These SDK settings remove generic filesystem reading,
`write`, `edit`, `bash`, and the general Main Sequence MCP connection, tools, and resource prompt.
This is a runtime tool restriction, not a prompt-only instruction.

The model-facing catalog retains:

- `alpaca__list_tools` and `alpaca__call_tool`, reaching only the declared Alpaca application's
  13 project tools;
- `alpaca__list_resources` and `alpaca__read_resource`, reaching only the skills/resources that
  same application advertises and verifying listed skill-file digests before returning content;
- a restricted `read` that can open discovered skill files and their supporting files, not
  arbitrary repository files or paths outside those skills. Since TAU 2.0.26 the runtime discovers
  skills only under `.tau/skills/`, so that index, which the prompt also lists, is the four
  business skills; the copied Main Sequence, platform, MetaTables, ms-markets and TAU development
  bundles under `.agents/skills/` are not skills of the Agent and the `read` refuses them;
- SDK `task_request_input` and `task_request_authorization` controls for the Agent's own A2A
  Task context. These are runtime controls, not general platform operations.

No project extension tools are installed. Runtime authentication, requester delegation, and the
Alpaca application's own platform access remain intact. The four business skills describe MCP
operations only; human/developer CLI guidance stays in the repository documentation. The
restricted Harness has no shell fallback. If an Alpaca tool cannot perform a requested operation,
the Agent must report that limitation.

These settings are declared on the Harness resource only; they do not affect the API, CLI, or
Jobs. A local launch must supply the same two settings explicitly. The hosted restriction takes
effect only after the changed workflow is pushed and a new Agent revision becomes active;
editing the workflow does not change an already deployed revision.

### Single-copy MCP skills — 2026-10-13

The four repository-owned skills cover the existing application tools. There were six until
[ADR 0027](adrs/0027_one_portfolio_path.md) removed two and renamed the portfolio skill.

| Skill | Tool workflows covered |
| --- | --- |
| `alpaca-asset-registration` | Asset queries and exact-symbol registration planning/execution. |
| `alpaca-account-workflow` | Account and holdings queries, account mutations, and site-based key requests. |
| `alpaca-stock-bars` | Stored bars configuration CRUD, including the `portfolios` source, resolution, dataset observations, and Job submission/status. |
| `alpaca-portfolios` | Rebalance configuration CRUD; reading portfolios, their weights, performance, statistics and runs; running them, and pausing, resuming or deleting them for their owner. |

They preserve credential, identity, bulk-operation, observed-weight, calendar and destructive-
confirmation boundaries. Each contains concrete `alpaca__call_tool` examples checked against the
actual tool schemas, query-operation guidance, bounded pagination and the shared
`alpaca_project_state` preflight. Portfolio guidance explains staleness, missing-price policy and
fractional commission units. CLI examples are removed from these Agent-facing skills.

Main Sequence 9.0.23 supports publishing a selected directory through
`mainsequence.server.mcp_skills.skills_extension`. The API configures it with `.tau/skills`, the
Agent's own skills folder, so the Agent's skills and the published skills are one set. Until
2026-10-27 it was `.agents/skills/mcp_tools`; the six skills moved directly under `.tau/skills`,
with no group folder, so their `skill://<name>/SKILL.md` URIs did not change. MCP clients use
`skills/list`, `skills/get`, and `resources/read`; the SDK owns the manifests and `skill://`
resources.
The modern `server/discover` capability advertises `io.modelcontextprotocol/skills`; legacy
initialization remains supported. Skill requests use the same request-identity protection as
tools. Local contract tests check all four manifests and exact source bytes/digests, reject private
SDK/platform resource URIs, require authentication and verify working-directory-independent
resolution. This local implementation does not establish that a deployed revision serves it.

The original six folders are moved, not copied. Their new folder names match their lowercase,
hyphenated frontmatter names. Neither the `.agents/skills` tree nor managed Main Sequence,
MetaTables, ms-markets, TAU or platform development guidance is published: `.tau/skills` holds only
the four skills, so publishing the whole folder publishes exactly them.

Serving skills is distinct from consuming them. TAU 2.0.14 fixes application skill consumption
through `alpaca__list_resources` and `alpaca__read_resource`
([released change](https://github.com/mainsequence-sdk/ms-tau-sdk/blob/v2.0.14/CHANGELOG.md),
[TAU #98](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/98)). It first negotiates modern
MCP discovery, where the Skills extension is advertised; older applications can still initialize
with the legacy protocol. These operations use the current call's application access, not a shared
cross-person catalog. Only advertised resources or listed skill files are read.

The earlier grouped-local-discovery issue [TAU #99](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/99)
is closed. The solution used here is MCP consumption of the single published directory. Tests now
exercise the real SDK application connector and resource tools over local HTTP: all four files must
be returned byte-for-byte with verified digests, private/traversal URIs must be refused, and
altered content must never reach the model. This does not certify a deployed revision or a live
model conversation.

TAU 2.0.21 added recursive local loading: a folder without a `SKILL.md` groups skills, and it read
the managed `.agents/skills/ms_tau_sdk/` skills and the other copied development bundles as the
Agent's own. TAU 2.0.26 reads skills only from `.tau/skills`, so the Agent's local skills are
exactly the four business skills. `tests/test_agent_resource_scope.py` loads a session through the
SDK's `tau_resource_paths` and pins that set, no context file and no root `AGENTS.md` text in the
prompt. The local-reader test checks that a synced `.agents/skills/ms_tau_sdk/` file and every
other file, such as `pyproject.toml`, `AGENTS.md` or `.tau/SYSTEM.md`, are refused.

### Application-tool timeouts

TAU 2.0.11 honors a tool's MCP `_meta["mainsequence.ai/timeout-seconds/v1"]` value. An undeclared
limit uses `TAU_MCP_TOOL_TIMEOUT_SECONDS`, which defaults to **60 seconds**, and no call may outlive
its turn. The current Alpaca MCP catalog declares no per-tool limit and the workflow declares no
override, so its calls use that default.

Since TAU 2.0.33 a tool call also tells the API how long it will still wait:

- **The deadline travels with the call.** Each call sends the time it has left, in milliseconds,
  in `Mainsequence-Request-Timeout-Ms`. The API's request identity reads it (Main Sequence 9.0.39
  and later). A Markets row call then waits for a Markets API that is still starting no longer
  than that, and answers `503 dependency_starting` with `retryable: true`
  ([ADR 0020](adrs/0020_dependencies_that_are_starting.md), decision 2).
- **A sleeping API is woken first.** When the API has scaled to zero, the Agent asks the platform,
  which wakes it, and waits for it before MCP opens. The wait is twice the release's
  `scaling.expected_cold_start_seconds`, at least 30 seconds, or 300 seconds while the platform
  states none. `TAU_MCP_APPLICATION_READY_TIMEOUT_SECONDS` would override it; the workflow does
  not set it.
- **A timeout during a start says so.** A call that times out while the API was still starting
  returns `failure: cold_start_timeout` with `runtime_access_state: waking`, and its message says
  the call may or may not have run. TAU never sends it again. `.tau/SYSTEM.md` has the Agent read
  the affected record before anything else, as after any timeout.

Bars and portfolio update tools submit Jobs and return their JobRun UID for status checks.
Some other mutations, including Asset registration and holdings capture, still execute
synchronously in the API and can exceed the tool deadline. A timeout is not proof
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

Model calls go through the platform's model relay (TAU 3.0,
[its ADR 0030](https://github.com/mainsequence-sdk/ms-tau-sdk/blob/v3.0.5/docs/adrs/0030-hosted-model-calls-go-through-a-relay.md)).
The hosted Agent never receives the `openai-codex` sign-in:

- The platform answers a session pass and the relay's address.
- The relay checks the pass and adds the real token.
- The platform refuses a hosted runtime that does not announce the relay
  (`tau_runtime_version_unsupported`), so a 2.x Agent cannot start a session.
- The Agent sets no `TAU_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID`, so the platform hydrates and
  relays, and this repository serves no relay.

The `harness_agent` declares no `runtime_resources` (TAU 2.0.30, its ADR 0026), so the Agent loads
all of `.tau/`: `SYSTEM.md` and the four skills.

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
`alpaca__list_tools` result (the 13-tool catalog), and that the API release advertises its MCP
connection before relying on it.

## Current local compatibility review

On 2026-11-28 the installed and locked versions are Main Sequence **9.0.47**, TAU **3.0.7**,
MetaTables (`mainsequence-metatable`) **0.1.88** and ms-markets **6.4.1**. The entries below
record how each range got there.

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
- The platform's Agent reads no longer return `runtime_update`, which Main Sequence 9.0.30 and
  earlier require, so the Agent lookup failed with `ValidationError` and the project
  configuration returned no Agent. Main Sequence 9.0.31 drops the field; on 2026-10-27 the range
  moved to `>=9.0.31`.
- On 2026-11-12 the range moved to `ms-markets>=3.4.0` for its `registerAccount` call (ms-markets
  ADR 0051), which registers an account, its held assets and its holdings in one Markets call. The
  resolution pulls MetaTables 0.1.63.
- On 2026-11-20 the range moved to `ms-markets>=4.2.0`, with no upper bound (the owner's
  decision), for the recipes that publish and read signal weights, portfolio weights and values
  ([ADR 0031](adrs/0031_markets_series_are_published_not_shared.md)). `uv.lock` and
  `requirements.txt` are regenerated once 4.2.0 is published.
- TAU 2.0.26 (ADR 0025) reads the deployed Agent's resources only from `.tau/`, so the six skills
  moved from `.agents/skills/mcp_tools/` to `.tau/skills/` in the same change.
- On 2026-11-23 the range moved to `ms-markets>=5.2.0`, with MetaTables `>=0.1.74` and Main
  Sequence `>=9.0.38`. ms-markets 5.0.0 has producers own their interpolated-price tables
  ([ADR 0029](adrs/0029_interpolated_price_tables_are_named_like_every_project_table.md)), and
  5.2.0 starts a new portfolio or calendar in a shared table at its own progress
  (MainSequenceMarkets#118 and #119).
- Later on 2026-11-23 the range moved to `ms-markets>=5.3.1`, with MetaTables `>=0.1.78`.
  ms-markets 5.3.0 retires portfolios and ends signals several in one call, deletes a portfolio
  selection in one transaction (MainSequenceMarkets#124, #126, #127), and retires the amount
  an account target stated (its ADR 0061; ADR 0023 amended). MetaTables 0.1.77 lets a run wait
  for a dependency update another process is running and reuses its connections
  (MetaTables#148, #149).
- Later on 2026-11-23 the range moved to `ms-markets>=6.0.0`, with MetaTables `>=0.1.79`.
  ms-markets 6.0.0 gives every order its own `Order` row and writes a batch's execution rows in
  one transaction with no updater run (its ADR 0064, MainSequenceMarkets#131); revision `0036`
  re-keys this application's instances. MetaTables 0.1.79 makes a head run wait for a run that
  holds its update, and a one-shot run send four requests (MetaTables#153, #154).
- On 2026-11-25 the range moved to `ms-markets>=6.2.0`, with MetaTables `>=0.1.84` and Main
  Sequence 9.0.41 in the lock. ms-markets 6.1.0 books every portfolio in a ledger, whose
  projections are the published weights and values (its ADR 0065), and runs every portfolio of
  a `run_portfolios` call in one MetaTables invocation (MainSequenceMarkets#133); revision `0037`
  creates this application's ledger. 6.2.0 has producers declare their tables as class
  statements (its ADR 0066) and can run a portfolio's nodes in parallel, each in a run of a node
  Job (its ADR 0067). MetaTables 0.1.84 moves its client modules under `metatables.client`,
  `metatables.models` and `metatables.contracts`.
- On 2026-11-26 the range moved to `ms-markets>=6.2.1`. 6.2.1 lets `InterpolatedPrices` take its
  assets from its source updater (MainSequenceMarkets#145), which
  [ADR 0033](adrs/0033_an_accounts_portfolios_share_one_price_chain.md) builds on, and scopes an
  asset-indexed updater's statistics through MetaTables' public hook (MainSequenceMarkets#144).
  Nothing in this application changes with it.
- Later on 2026-11-26 MetaTables moved to `>=0.1.85`, which starts and ends a run under a shared
  guard with row locks (MetaTables#167).
- Later on 2026-11-26 the range moved to `ms-markets>=6.2.3`, the release the Environment's Markets
  API runs. 6.2.2 requires MetaTables 0.1.85; 6.2.3 lets Markets' revision 0039 take an
  Index-value table its database already holds, which had stopped every 6.2.x rollout at its
  migration (MainSequenceMarkets#147). Nothing in this application changes with either.
- Later on 2026-11-26 MetaTables moved to `>=0.1.86`, which retries the release of a migration's
  runtime hold and never fails a migration on it (MetaTables#169): a 503 on that release had
  failed the migration Job of `4f1b17c` after it committed revision 0037.
- Later on 2026-11-26 the range moved to `ms-markets>=6.2.4`, which Production's Markets API
  runs. A ledger now books a rebalance at the prices it was sized with, even ones from before its
  opening (MainSequenceMarkets#150), and a MetaTables write without an answer no longer makes a
  route answer a bare 500: the bulk deletes confirm it with one read and report it in `failed`
  (MainSequenceMarkets#151). Each ledger still opens at the last session close before its signal.
- Later on 2026-11-26 the range moved to `ms-markets>=6.2.5`, which Production's Markets API
  runs. A portfolio's interpolated prices now read their source bars in several reads, each
  within MetaTables' budget of 100,000 rows for one read (MainSequenceMarkets#155). Before, an
  account's first run with about 45 new daily assets would have failed with
  `TransferLimitError`. Issue #49's deadline failure had already been fixed in 5.3.0, which reads
  10,000 rows a page (MainSequenceMarkets#122). Nothing in this application changes with it.
- On 2026-11-27 the ranges moved to Main Sequence `>=9.0.43`, TAU `>=3.0.5,<4`, MetaTables
  `>=0.1.87` and `ms-markets>=6.3.0`, for telemetry: each hop is a span in a platform trace,
  written as log lines (Main Sequence ADR 0042). The API's request identity makes the launcher's
  request span the current span, a Job run continues the trace its `TRACEPARENT` names, every
  call to the platform writes a `dependency.call.completed` or `dependency.call.failed` line, and
  every log line carries `trace_id`, `span_id` and `parent_span_id`. The Agent's runtime records
  its request, run, turn and tool calls as spans of the caller's trace. The SDK sets no
  OpenTelemetry provider and the platform exports no OTLP, so this application configures
  nothing. ms-markets 6.3.0 has a virtual fund name its target allocation (its migration `0040`,
  MainSequenceMarkets#157); this application allocates funds only through
  `allocateAccountVirtualFunds`, so nothing here changes, but the Environment's Markets API must
  run 6.3.0 first. TAU 3.x is also the first runtime the platform starts: since 2026-11-13 its
  Tau bootstrap refuses a hosted runtime that does not announce `supported_model_relay_versions`
  `[1]` (`tau_runtime_version_unsupported`), which 2.0.26 never sends. This Agent sets no
  `TAU_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID`, so the platform's model relay applies and
  `create_app()` needs no change. In tests, TAU 3.0's application access carries `session_uid` and its
  bootstrap `runtime_resources`.
- Later on 2026-11-27 the range moved to `ms-markets>=6.3.1`, which Production's Markets API
  runs. 6.3.1 only requires Main Sequence 9.0.43 and MetaTables 0.1.86 and changes no behaviour,
  so nothing in this application changes with it.
- On 2026-11-28 the ranges moved to TAU `>=3.0.7,<4`, Main Sequence `>=9.0.47` and MetaTables
  `>=0.1.88`:
  - **The Agent's answers.** Until 2026-11-27 every relayed model answer reached the Agent cut to
    its first chunk ([ms-tau-sdk#150](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/150)).
    The platform's `GZipMiddleware` compressed each chunk of the relay's async stream as its own
    gzip member, and httpx decodes only the first, so a Codex turn ended as `stop` with no content
    and zero usage. The platform fixed it
    ([tdag-django#1307](https://github.com/Main-Sequence-Server-Side/tdag-django/issues/1307));
    nothing in this application changes for it.
  - **TAU 3.0.6 and 3.0.7.** A session keeps its lease through a platform restart, and only a
    model's reply is logged as a model call. An application call right after a redeploy, when
    the platform answers `unavailable` with `retry_after_ms`, now waits instead of failing.
    `TAU_LOCAL_MCP_APPLICATIONS` lets a local Agent use a local API; this repository does not set
    it.
  - **Main Sequence 9.0.46 and 9.0.47.** The caller's remaining time is public as
    `mainsequence.client.remaining_request_seconds`, which `alpaca_connectors.dependencies` now
    imports instead of the private `mainsequence._request_deadline`. Its fallbacks for an SDK
    before 9.0.39 are gone. 9.0.47 removes `reads_as_caller()`, which this application never used.
  - **MetaTables 0.1.88.** Its API refuses every run start from an older client (its ADR 0034),
    so this application's Jobs need 0.1.88 once that API runs it.
- Later on 2026-11-28 the range moved to `ms-markets>=6.4.0`, which sends each Markets API call
  through the SDK's `ResourceRelease.call` (MainSequenceMarkets#160). A call is now a span of the
  caller's trace and keeps to the caller's deadline. Finding the Markets API no longer asks for
  its access or takes a `timeout`, so `alpaca_connectors.dependencies` changes with it
  ([ADR 0020](adrs/0020_dependencies_that_are_starting.md), amendment of 2026-11-28):
  - the startup prewarm asks once for the Markets API's runtime access;
  - the Job gate waits with the SDK's `wait_for_runtime_access`;
  - a Markets start is recognised by the SDK's errors alone.
- Later on 2026-11-28 the range moved to `ms-markets>=6.4.1`, which Production's Markets API
  runs. 6.4.1 changes documentation only; its producer-boundary skill now says that Markets
  calls are spans of the caller's trace and that `ReleaseCallError` reaches the caller unchanged.
  Nothing in this application changes with it.
- **The Agent answers again.** On 2026-11-28, after `bce4241` deployed, a read-only Message to the
  Production Agent answered (A2A 200) in session `d8bb353d-4d28-4288-9d21-24a6e8536453`:
  - the relayed model call recorded real usage (1,971 tokens in, 88 out);
  - the Agent listed the Alpaca tools and called `alpaca_project_state`;
  - it replied `relay-ok 0 accounts`, ending with `stop`.

`pyproject.toml` keeps non-exact compatibility ranges (`mainsequence[mcp]>=9.0.47,<10`,
`mainsequence-metatable>=0.1.88,<0.2`, `ms-tau-sdk>=3.0.7,<4`, and `ms-markets>=6.4.1` with no
upper bound since [ADR 0031](adrs/0031_markets_series_are_published_not_shared.md)), while
`uv.lock` and the exported `requirements.txt` record the resolution. On 2026-10-14 Main Sequence
had moved from 9.0.23 to 9.0.24, which stopped scaffolding `AGENTS.md` and ships its guidance in
the SDK skills, and MetaTables from 0.1.34 to 0.1.35. The earlier SDK upgrade moved the official
MCP dependency from **1.30.0** to **2.3.0**, with its required transport dependencies.
With the 2026-11-12 versions the suite passed 664 tests plus 97 subtests with network access
blocked. On 2026-11-27 the suite passes offline: 976 tests plus 84 subtests passed with network
access blocked, and 6 were skipped (the opt-in live end-to-end test and five PostgreSQL tests).

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
application-skill contract, not a copy or custom loader. The resource-scope test came with TAU
2.0.26; the 2026-10-27 count above includes it.
The MCP catalog then had nine read-only tools and eight mutation/multi-operation tools; since
ADR 0027 it has seven and six. This local review alone does not certify a deployed Agent or hosted
migration completion.
The MetaTables SDK compatibility check and installed dependency consistency check also passed.

Tool-restriction regressions exercise the installed TAU runtime's catalog assembly with mocked
backend/model I/O: only the four Alpaca application tools, restricted local reader, and A2A Task
controls remain. The local reader indexes only the four `.tau/skills` files. Local HTTP tests
additionally verify skill consumption through MCP, refusal of private files and digest
mismatches, and separate caller identities on consecutive calls. Hosted caller tests sign
identity-only assertions, and an assertion or requester statement that lists `team_uids` is
refused with 401.

The requested development skill bundles were explicitly refreshed from the installed packages on
2026-10-21: Main Sequence 9.0.30, TAU 2.0.23 and MetaTables 0.1.51, and the platform skills from
the development backend's catalog, which no longer ships `code_repository_design`. The existing
ms-markets 2.2.10 bundle was left unchanged. The `ms_tau_sdk` bundle was synced again from TAU
2.0.26 on 2026-10-25. On 2026-11-27 every bundle was refreshed again: Main Sequence 9.0.43,
TAU 3.0.5 (`ms-tau skills sync`), MetaTables 0.1.87, ms-markets 6.3.1 and the platform skills.
On 2026-11-28 the Main Sequence 9.0.47, TAU 3.0.7 and MetaTables 0.1.88 bundles were refreshed,
then the ms-markets bundle from 6.4.1 and the platform skills, which had not changed. Each bundle's `PINNED_FROM.txt` records its matching version. The
repository-owned business skills remain single-copy. On 2026-10-14 `AGENTS.md` lost the retired SDK
block and was cut to its developer rules, the operator manual moved to the
[operator guide](operations/operator-guide.md), and `.tau/SYSTEM.md` became the runtime prompt.
These changes are recorded on `main`;
this local verification did not change hosted schema or certify a deployment.

The earlier 2026-10-11 authenticated local API smoke test initialized a real MCP session,
discovered the 17 tools of that catalog, and read project state and Accounts against the migrated
local MetaTables runtime. Invalid tool arguments produce a structured error without echoing their
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
long bars and portfolio computations through the existing Job tools. Never replay a
Message after an ambiguous transport outcome. Local TAU has no platform Message limit, so a
local success alone does not establish hosted timing compatibility.

After publishing an SDK update, inspect the new exact-commit workflow and both active release
revisions before testing the deployed API and Agent; an earlier successful revision is not proof
of the new dependency image. Preserve the migration gate and Team grants during this verification.

The Environment's Team access was repaired without direct workload grants. The existing migration
Job applied revisions `0015`–`0017`, and the subsequent exact-commit image → migration → API workflow
succeeded. Agent discovery reported **Alpaca Agent**, a current Harness runtime, the requested Luna
model, and the declared Alpaca MCP application. The Harness was not added to the table-writer Team.

The deployed Agent discovered all 17 project tools of that catalog and read project state and
Accounts. A distinct same-session follow-up returned the remembered results without repeating
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
