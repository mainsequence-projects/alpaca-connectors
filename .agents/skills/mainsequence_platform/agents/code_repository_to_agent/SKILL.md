---
name: code-repository-to-agent
description: Prepare or review an existing Main Sequence CodeRepository as a truthful repository-backed Harness Agent by defining repository instructions, repository-owned skills, and the repository source card, then deploy it through the harness_agent workflow.
---

# Main Sequence CodeRepository To Harness Agent

To use project FastAPI MCP tools, add optional `spec.mcp_applications` to the
Harness Agent workflow: `{resource: orders}` in the same file or
`{repository: Inventory Service, resource: inventory}` in the same Environment.
Targets must declare `mcp_enabled: true`. Resource keys are unique tool prefixes
matching `^[a-z][a-z0-9_]{0,39}$`, excluding `mainsequence`. Configure no URL or
UID. Bootstrap supplies names and resolved release UIDs; Tau registers
`<name>__list_tools` and `<name>__call_tool` and resolves access as the authenticated
User or verified requester per call. Registration grants no permission and does
not limit other platform-client calls. Invalid references fail validation or
session loading. Read `resource-release` FastAPI MCP capability for connection
requirements. Verify that the installed Tau version supports this registration
and delegation contract; backend bootstrap data alone does not install the tools.

Use this skill to turn an existing Main Sequence CodeRepository into a
repository-backed Harness Agent. Start with `code-repository-design` to obtain
an accepted architecture and CodeRepository Blueprint when design work remains.

The calling agent inspects and edits the repository. MCP supplies platform
meaning and approved operations.

## Architecture

A Harness Agent executes verified repository behavior from the branch's normal
code repository image. Django creates the Agent identity and a `harness_agent`
ResourceRelease when it applies the repository workflow. The runtime library
installed by the project owns its own application layout, startup, configuration,
local-development, and debugging instructions.

Each CodeRepositoryBranch owns at most one Harness Agent. The branch supplies
the source, dependency lock, indexed application resource, code repository image,
commit, Organization, and Organization Environment.

The backend derives runtime identity and deployment state. The workflow result
returns the created Agent UID and ResourceRelease UID. The project lock selects
the runtime version, and the Harness Agent release owns automatic redeployment
policy.

Django Pod Manager is the deployer: it resolves the exact-commit application
resource, owns image selection/building, creates DeploymentRuns and revisions,
and invokes the Harness Agent deployment adapter. The Agents application owns
the branch Agent's semantic identity, Agent Card, and provider/model/thinking
defaults. Repository YAML supplies neither side's UIDs.

## Runtime Implementation Handoff

Platform skills do not reproduce runtime-library instructions. When developing
a TAU-based Harness Agent, use the skills packaged by the exact `ms-tau-sdk`
version installed in the project. Synchronize them only when the user requests
that repository mutation:

```bash
uv run ms-tau skills sync --path .
```

Then read the relevant skill under `.agents/skills/ms_tau_sdk/`. The SDK-owned
bundle covers repository integration, local development and debugging, project
customization, and TAU's A2A host adapter. If the command is unavailable, report
that the project has no compatible installed SDK before prescribing runtime
files or settings. Package installation and runtime startup must not copy these
skills implicitly.

## Repository Artifacts

Prepare this repository-owned structure:

```text
AGENTS.md
.agents/
├── agent_card.json
└── skills/
    └── <repository-skill>/
        └── SKILL.md
```

The managed `.agents/skills/mainsequence/` tree supplies platform and SDK
guidance. A separately synchronized `.agents/skills/ms_tau_sdk/` tree supplies
version-matched TAU implementation guidance. Repository capability declarations
come from repository-owned skill directories outside both managed namespaces.

## Procedure

1. Inspect the repository purpose, dependency lock, application resources,
   tests, and agent artifacts.
2. For a TAU-based implementation, explicitly synchronize and follow the
   installed `ms-tau-sdk` skills instead of reconstructing its runtime contract
   here.
3. Define the Harness Agent's role, supported work, inputs, outputs, side
   effects, safety conditions, and observable success criteria.
4. Map every capability to executable repository behavior, documentation, and
   focused validation.
5. Create or update the exact `## CodeRepository-Specific Instructions`
   section in `AGENTS.md`.
6. Create or update focused repository-owned skills under `.agents/skills/`.
7. Create or update `.agents/agent_card.json` from the verified repository
   behavior.
8. Validate instructions, skills, executable behavior, source-card entries,
   paths, inputs, outputs, and side effects as one coherent contract.
9. Keep the declared `source_path` in the commit you will push. The push makes
   Django index it as an exact-commit FastAPI `CodeRepositoryResource`; do not
   discover or copy its UID. No local command indexes it: `code-repository
   sync` only refreshes dependency files and does not commit or push.
10. For deployment work, use `code-repository-workflows` to add one `kind: harness_agent` declaration
   for the branch and validate it through Django.
   Do not create a health or readiness route: the common launcher supplies
   `GET /ms-health-deployment`, and `ms-tau-sdk` registers its predicate
   automatically.
11. Commit the repository changes, push the branch, and observe the
   resulting DeploymentRun.
12. Use `log-exploration` for the exact Agent, ResourceRelease, or DeploymentRun
   when runtime diagnosis is required.

For planning requests, return this procedure adapted to the repository and
identify the files each step will produce.

## Repository Instructions

The `## CodeRepository-Specific Instructions` section records:

- repository purpose and operating boundary;
- work the Harness Agent performs;
- escalation conditions and responsible owner;
- repository-owned skills used for specialized work;
- repository-specific validation commands;
- mutation and approval requirements; and
- safety rules tied to actual repository behavior.

Write these instructions specifically for the repository. Reference managed
Main Sequence guidance by skill name and keep repository details in this
section.

## Repository-Owned Skills

Create one narrow skill for each coherent user workflow. Each skill records:

- a precise trigger;
- required inputs and produced outputs;
- the executable commands or structured tools it uses;
- preconditions and authorization requirements;
- observable side effects;
- validation commands;
- completion criteria; and
- escalation conditions.

Ground each claim in implemented repository behavior. When a required behavior
still needs implementation, record that implementation gap and obtain the
appropriate authorization before adding it.

## Runtime-Specific Behavior

Use the installed runtime library's version-matched skills for project tools,
configuration, startup, local execution, and debugging. This platform skill
does not prescribe their file layout or environment variables. Whatever runtime
the repository uses, canonical MCP and DRF operations remain authoritative for
live platform state, and the backend-derived Organization Environment remains
the deployed Harness Agent's authorization scope.

## Repository Source Card

Create `.agents/agent_card.json` as the repository-authored source definition.
It contains stable identity, description, version, response-kind profile, and
repository-owned skill descriptions.

The card is a deployment prerequisite. Django loads the indexed strict path at
the exact repository-event commit, requires non-empty top-level `name` and
`description`, and persists the normalized card before creating the Agent. It
synchronizes those fields into the existing branch Agent on later commits. Do
not rely on repository names, branch names, runtime labels, or generated text
as presentation fallbacks.

```json
{
  "name": "Portfolio Risk Analyst",
  "description": "Reviews portfolio risk using verified repository behavior.",
  "version": "1.0.0",
  "capabilities": {
    "extensions": [
      {
        "uri": "https://mainsequence.ai/a2a/extensions/response-kind/v1",
        "description": "Select a completed message or asynchronous task response.",
        "required": false,
        "params": {
          "supportedResponseKinds": ["message", "task"],
          "defaultResponseKind": "message"
        }
      }
    ]
  },
  "skills": [
    {
      "id": "portfolio-risk-review",
      "name": "Portfolio Risk Review",
      "description": "Review exposures and report material risks.",
      "tags": ["portfolio", "risk"],
      "examples": ["Review the current portfolio risk concentrations."],
      "path": ".agents/skills/portfolio-risk-review/SKILL.md"
    }
  ]
}
```

Use meaningful human-facing names, stable kebab-case skill IDs, factual tags,
and repository-relative paths to existing skill files. Describe response kinds
implemented by the runtime and backend. Use `message` as the standard response
kind and add `task` when asynchronous task behavior is implemented.

At deployment, the Harness Agent runtime materializes the complete A2A card
with concrete URLs, protocol bindings, enforced security, transport and media
capabilities, and the supported response-kind intersection.

## Harness Agent Workflow

For accepted idle-latency requirements, record the desired replica floor and
its resource-cost rationale in existing deployment expectations; for accepted
throughput requirements, record the desired replica ceiling and the per-replica
capacity it assumes. Current workflow API `2.3.0` accepts `min_scale` (`0`
through `50`, default `0`) and `max_scale` (`1` through `50`) for FastAPI and
Harness Agents; `min_scale` never exceeds `max_scale`, and an omitted
`max_scale` defaults to `10` for FastAPI and `5` for Harness Agents. A floor of
`1` keeps the deployed revision warm; it does not guarantee zero downtime or
bypass readiness and runtime admission. A ceiling only bounds how many replicas
autoscaling may start; it does not reserve cluster capacity.
The release owns this policy; the Agent Card, session runtime configuration,
and workflow `env_vars` do not. PATCH/MCP updates save desired state and the next
successful workflow-driven deployment applies its immutable revision snapshot.
Update omission preserves each value and an explicit zero floor resets
scale-to-zero. Runtime release and bound Harness Agent summaries expose both
desired values as numeric stats, including a zero floor; Static Sites and
unbound Agents omit them.

For accepted per-replica capacity, record the number of requests one replica
serves at once. Current workflow API `2.3.0` also accepts, for FastAPI and
Harness Agents, `container_concurrency` (`1` through `1000`, the hard limit of
requests in flight per replica), `concurrency_target` (`1` through `1000` and at
most `container_concurrency`, the per-replica autoscaling target) and
`timeout_seconds` (`1` through `600`). Each is optional: omitted on creation it
keeps no limit, Knative's default target and the platform's 300-second
timeout; on update omission preserves it and explicit null resets it. An open
WebSocket counts as one request for its whole lifetime, so a FastAPI release
that serves WebSockets sizes `container_concurrency` for its connections too.
Total capacity is about `max_scale` x `container_concurrency`.

Use the backend-provided workflow template as the source for the current field
shape. The deployment intent has this structure:

```yaml
- key: repository-harness-agent
  kind: harness_agent
  spec:
    source_path: api/agent/main.py
    llm_provider: openai
    llm_model: gpt-5.4
    llm_thinking: medium
    automatic_deployment: true
    automatic_redeployment:
      enabled: true
      tag_regex: null
    revision_retention_count: 3
    env_vars:
      - name: FEATURE_MODE
        value: conservative
```

Set `source_path` to the repository-relative FastAPI application entry point.
The installed runtime SDK's skills define the application module and
construction contract. Django resolves the internal indexed resource and
ordinary code repository image at the exact repository event commit, creates the Agent
identity, creates the `harness_agent` release, and binds the two records.

Declare `llm_provider`, `llm_model`, and `llm_thinking` explicitly. Django
validates that combination through the organization-aware provider catalog and
persists it on the Agent before deployment. Use `llm_thinking: off` for a model
without configurable reasoning. These settings configure runtime behavior and
never identify the deployment.

Set `automatic_deployment` to enable repository-event deployment. Configure
`automatic_redeployment.enabled` and `tag_regex` as the release's update
policy. An omitted or null `tag_regex` accepts every pushed commit while
enabled; a bounded regular expression full-matches short tags the repository
creates itself. Only the workflow file sets these values.

Place plain runtime configuration values in `env_vars`. Store protected values
through the platform's Secret and Constant facilities.

The Agent runs as its own workload User, with no access until it is granted.
Declare the objects it needs under the resource's `access`, or grant them by
hand; the code-repository-workflows skill (Workload Identity, Declared Access)
explains both. An Agent accesses Secrets through ordinary effective-user permissions.
Anyone with edit on the Agent can change code that uses its access; granting
access therefore trusts those editors. Its backing release follows the Agent's
sharing. Read `resource-release` Runtime authentication and recovery before
upgrading or redeploying pinned runtime SDK dependencies.

## Validation

Complete these checks before reporting success:

1. Verify the selected runtime implementation using its installed,
   version-matched skills.
2. Parse `.agents/agent_card.json` as a JSON object.
3. Verify its name, description, version, capabilities, and skill entries.
4. Verify every skill ID is unique and every skill path resolves to a
   repository-owned Markdown file.
5. Verify every skill's commands and executable tools exist and match their
   documented schemas.
6. Compare every capability claim with implementation, documentation, and
   focused tests.
7. Verify `AGENTS.md`, repository skills, executable tools, and the source card
   describe the same behavior and safety boundaries.
8. Run the repository's focused validation commands.
9. Verify Django indexed `source_path` as the expected exact-commit FastAPI
   application; never record its UID in workflow YAML.
10. Validate the `harness_agent` workflow through Django for deployment work.
11. Inspect the workflow result for the backend-created Agent UID,
    ResourceRelease UID, and DeploymentRun UID.
12. Verify the live Agent card, name, and description equal the indexed source
    card for that exact commit.
13. Observe DeploymentRun completion and runtime availability independently
    from repository-source validation.

## Result

Report:

1. verified repository purpose and Harness Agent role;
2. repository-owned files created or changed;
3. executable behaviors connected to each skill;
4. validation commands and results;
5. remaining implementation gaps or escalation items; and
6. the `harness_agent` workflow result and observed DeploymentRun when
   deployment was requested.

## Acting for the requester

An Organization admin may enable `acts_for_requester`. Verified delegation uses
the person's ordinary read/write permissions, including administrative roles,
within the runtime Environment. Absent delegation uses the workload's own
permissions; invalid delegation is rejected. No extra Agent-approved tools or
exclusions apply to Secrets, credentials, billing or admin operations.

The trusted host supplies private session/lease proof when acting for a person;
never put it in model arguments or logs. Access is rechecked per call during
active work, for at most 24 hours from the original request. The same MCP catalog
requires authentication for people and workloads, including discovery. Prompt
injection can cause persistent changes within those rights. Use the shared client
statement in `code-repository-workflows`.
