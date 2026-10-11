---
name: code-repository-design
description: Design, explain, review, and maintain a Main Sequence CodeRepository architecture and its connected Blueprint, including one branch-owned repository-backed Harness Agent and the standard deployment handoff.
---

# Main Sequence CodeRepository Design

Act as the CodeRepository architect. Translate user intent into a connected,
CodeRepository-owned Blueprint that another agent can implement without reconstructing
the architecture from unrelated lists.

The MCP server delivers this skill, the platform ontology, and approved
operations. It does not contain an LLM, plan the CodeRepository, or write the
Blueprint. Perform the reasoning in the calling agent.

## Preserve The Ownership Boundary

Own:

- CodeRepository intent and success criteria;
- CodeRepository-domain concepts, relationships, and invariants;
- architectural selection and rationale;
- cross-component dependencies;
- Blueprint creation, review, change, reconciliation, and handoff.

Do not own:

- Python, SDK, package, migration, or virtual-environment mechanics;
- local Git and filesystem operations;
- concrete implementation code;
- DRF persistence or platform runtime state;
- deployment execution;
- secret values or runtime credentials.

Use the SDK and domain execution skills after the design is accepted. For a
static-site frontend, the complete version-matched skill bundle shipped by the
CodeRepository's installed `@dev-mainsequence/command-center-sdk` package owns the
frontend implementation. The MCP `resource-release` skill owns shared release
creation, configuration, deployment, and state observation; the `static-site`
skill owns only the static-specific capability and frontend handoff.

A Harness Agent is debugged directly from its repository workspace and dependency
lock; there is no special agent-runtime image to discover or pull through MCP.
Runtime-specific setup and debugging belong to the installed runtime SDK's
version-matched skills.

When accepted CodeRepository intent requires financial-markets functionality, select
`ms-markets`, record the selection and rationale in `decisions` and the
affected components' `depends_on` entries, and defer all financial-market
domain and implementation guidance to the skills shipped by `ms-markets`.

## Load Platform Meaning

Read `mainsequence://platform/ontology` before selecting platform concepts.
Keep these distinctions:

- `CodeRepository` is the logical platform aggregate. It owns the canonical
  user-facing name, lifecycle, labels, sharing, and its complete collection of
  CodeRepositoryBranches.
- `GitHubRepositoryBinding` is the provider/source-control record. Repository detail
  exposes its owning logical CodeRepository UID and never selects an entry, main,
  oldest, or current CodeRepositoryBranch.
- `OrganizationEnvironment` is the canonical operational partition of
  the platform. For CodeRepository-owned code and resources, the exact Git branch is
  the repository-side partition marker and `CodeRepositoryBranch` is the durable
  platform context that binds one logical CodeRepository to exactly one Environment.
  Another provider branch gets a different CodeRepositoryBranch UID while retaining
  the same logical CodeRepository UID, allowing one CodeRepository to participate in several
  Environment partitions without duplicating the CodeRepository.
- Never model a branch-owned object against `CodeRepository` alone and then infer an
  active, default, main, or current branch. Resolve the exact persisted
  `CodeRepositoryBranch`; all of its Jobs, images, releases, runtimes, Harness
  Agent, and repository-derived resources stay in that branch's Environment.
  A commit SHA shared by several branches does not merge their partitions.
- A branch can own at most one Harness Agent. Django creates its `Agent`
  identity only after resolving the indexed `.agents/agent_card.json` for the
  exact deployment commit. The required card owns the human-facing name and
  description; repository and branch labels are not presentation fallbacks.
  Django derives the Organization Environment from the exact branch and binds
  the Agent to one `harness_agent` ResourceRelease. The user does not provide an
  Agent UID or select an Environment in deployment intent. Runtime callers use
  the Environment derived from the authenticated Agent/release binding;
  environment membership is not blanket session or task authorization.
- `code_repository.create` establishes the logical CodeRepository and canonical
  production `main` CodeRepositoryBranch and never accepts a branch name. It may accept
  one visible `bootstrap_organization_environment_uid`; the backend derives that
  Environment's required branch and, when non-main, creates it from the exact
  initialized main commit without a second scaffold. The UID is creation context,
  not CodeRepository or Blueprint ownership. After bootstrap, a signed
  provider push links an existing branch automatically only when the
  Organization administrator has already created the exact matching
  environment; do not create a second logical CodeRepository for it. Git never creates
  the environment, and no manual branch-import workflow is accepted. The
  backend assigns each branch to the same-Organization environment whose
  immutable required branch exactly matches.
- CodeRepositoryBranch provisioning is durable and has exactly `CREATING`,
  `READY`, or `FAILED` state. `READY` is equivalent to the compatible
  `is_initialized=true` projection. Only `CREATING` is pollable; `FAILED` is
  terminal until an authorized explicit DRF retry and retains its latest setup
  JobRun UID plus stable failure fields. Recovery resolves stale state from the
  persisted JobRun and never launches an automatic retry. Do not infer
  readiness from CodeRepository creation returning successfully.
- `OrganizationEnvironment` is shared by exact compatible
  CodeRepositoryBranches from one or several CodeRepositories. It owns platform
  execution and configuration context.
- `MetaTable` is an independent MetaTables application catalog boundary for a
  physical table. The application owns storage registration, catalog grants and
  table operations without platform Organization or Environment ownership.
  CodeRepository-owned shapes are authored in SQLAlchemy metadata and use
  SQLite, PostgreSQL, or TimescaleDB. Delegate storage configuration and
  capabilities to the owning application's installed guidance.
- `TimeIndexMetaTable` is the `MetaTable` specialization for time-indexed
  storage. It owns the time index, cadence, ordered identity dimensions,
  partition strategy, and time-series progress behavior.
- `TimeIndexTableUpdater` is deterministic update logic that produces or maintains
  `TimeIndexMetaTable` data; its database identities are derived from its
  input and output MetaTables.
- `Job` is a CodeRepository-bound execution definition with a repository-relative
  `.py` or `.yaml` execution path, runtime resources, exactly one ownership-typed image
  identity through the exclusive public/Organization relation pair, an exact
  full commit for CodeRepository code, optional future exact-event image promotion,
  and an optional schedule. `JobRun` is one execution and freezes that image,
  digest, and commit before launch. A branch-owned Job, Harness Agent, or
  runtime ResourceRelease may execute only a
  digest-pinned CodeRepository image whose verified source provenance matches the
  exact CodeRepositoryBranch and commit. A create request first persists a stable
  pending image and typed build run without remote repository access. Celery then
  admits source by cloning once, resolving and proving the full commit is reachable
  from that branch's exact remote ref, deriving SDK metadata from the same admitted
  archive, and staging one normalized checksummed compressed context in the output tenancy.
  Every image-build run protects that immutable relational artifact; GCP, Azure
  and AWS derive request locations from it, and retries never reclone Git,
  reinterpret provider strings, or choose branch state. CodeRepository authors and
  MCP callers never provide a bucket, object key, source URI, or signed token.
- Image ownership is explicit: public bases, tools, and bundles are
  `PublicCatalogImage`; Organization build outputs are
  `CodeRepositoryJobImage` descendants of `OrganizationImage`. There is no generic
  persisted `Image`, generic UID resolver, or cross-root discovery path.
- Every image-dependent `DeploymentRun` binds its exact image roles through
  typed `DeploymentRunImageDependency` rows. Active runs retain the live
  relation; terminal history may retain a typed tombstone after canonical
  image deletion. Every retained runtime `ResourceReleaseRevision` separately
  pins its exact `CodeRepositoryJobImage`, including rollback revisions that
  are neither active nor desired. When runtime retention removes an old
  revision, the backend submits that former image to canonical guarded deletion;
  shared images remain preserved. One complete
  `CodeRepositoryImageBuildRun` owns immutable build identity, provider request and
  operation state, a protected exact build-context artifact, reconciliation
  deadlines, and failure. The run commits before remote preparation, becomes complete
  before provider submission, and records preparation failure durably;
  database state is the durable queue and Celery is only a wake-up. Generic
  JSON never owns image UIDs, build-context artifact identities, source URIs,
  digests, provider handles, or readiness.
- Concurrent target services requesting the same exact image build converge on
  one canonical attempt and attach independent parent dependencies. Ambiguous
  submission remains on that attempt during a bounded reconciliation window
  and is never blindly retried. If no provider handle or output can be adopted
  by the deadline, that attempt and its awaiting dependencies fail; a delayed
  observation cannot rewind it. Execution accepts only verified digest-pinned
  dependencies and never a `latest` tag.
- When a runtime ResourceRelease is declared in `.mainsequence/workflows` with
  automatic deployment enabled, CodeRepository design does not select or require an
  image. The workflow ignores any image UID and validation accepts the
  declaration without one. On first application the backend resolves the exact
  repository-event image identity before materializing the backing Job; the
  target remains non-runnable until that image is verified and digest-pinned.
  Initial application owns one `source=create`, `operation=build_and_deploy`
  run and waits for that image when necessary. Later repository events own
  policy evaluation and use `source=repository_event` runs. New user-facing
  ResourceReleases are created only from repository workflow declarations;
  callers do not select a ready image for direct creation.
- Workflow APIs `2.0.0`, `2.1.0`, `2.2.0`, and `2.3.0` can carry non-secret target-owned `env_vars` for Jobs
  and ordinary runtime ResourceReleases. Current API `2.3.0` also accepts them
  for `harness_agent`. Static sites use
  `build_environment`. These literals configure only the declared target or
  its backing Job: they do not create or resolve platform Secrets/Constants,
  select an Organization Environment, write branch-wide configuration, or
  enter code-repository-image builds.
- Do not design branch-wide `env_vars`. CodeRepository create rejects that retired
  input, CodeRepositoryBranch stores no arbitrary environment mapping, and Jobs do
  not inherit one. Environment Constants and Secrets require explicit resolution and
  are not automatic process injection.
- A deployed branch-owned runtime receives a backend-derived public context
  containing logical CodeRepository UID, exact CodeRepositoryBranch UID, descriptive branch
  name, and Organization Environment UID. That authenticated target chain is
  the resource-composition and routing authority, not the action principal.
  Every runtime credential authenticates its workload User. Without delegation,
  that User's ordinary permissions apply; verified delegation selects the person,
  including their admin roles, while retaining the workload for runtime proof,
  billing and audit. Invalid delegation is refused. Git is used only
  by a genuine local checkout to discover a persisted CodeRepositoryBranch; a
  deployed image never requires `.git` and cannot select another branch or
  environment.
- For Agent execution, distinguish the authenticated workload, effective User
  and AgentSession owner. `AgentSession.created_by_user` owns the invocation and
  its authorized model-provider selection. An A2A child and handle inherit that User from
  the exact immediate parent session, whose Agent proves the calling service.
  Every target runtime hydrates independently after exact session
  authorization; credentials never travel in A2A content and service ownership
  is never a credential fallback.
- SDK application code never supplies deployed runtime mode, CodeRepositoryBranch,
  repository branch, or Organization Environment. The SDK installs context
  only from an authenticated startup or credential-exchange response and omits
  branch/environment selectors on deployed requests. Reserved process
  environment values are backend-written transport and diagnostics, not a
  context activation mechanism. Local Git selection remains repository
  navigation; the SDK derives and inserts any required local wire context
  internally.
- An API is a consumer and composition surface, not hidden producer logic. One
  FastAPI API may own both ordinary HTTP/REST routes and
  FastAPI/Starlette WebSocket routes when they share one application factory
  and deployment lifecycle. They deploy as one existing `fastapi`
  ResourceRelease, image, revision, stable URL, Knative Service, and Uvicorn
  process; WebSocket is not a separate release kind or deployment target.
- A CodeRepository CLI command is an executable CodeRepository interface, not the platform
  permission authority.
- `code_repository_to_agent` exposes verified CodeRepository behavior as truthful
  CodeRepository-agent skills. A skill must be backed by executable repository
  behavior; the installed runtime library owns its tool-registration and
  extension mechanics. This is not generic Agent administration.
- `AutomaticRedeploymentPolicy` is target-owned and CodeRepositoryBranch-scoped. It
  refines the automatic-deployment master switch for one standalone Job,
  ResourceRelease, or Harness Agent release; it is never a shared
  CodeRepositoryBranch-wide rule. A Job policy controls only future qualifying exact
  repository-event promotions and never means a mutable latest image.

Use the platform ontology for global platform nouns. Define the CodeRepository's own
business concepts inside the Blueprint.

Every ResourceRelease independently owns immutable revisions, nullable
`active_revision`/`desired_revision` pointers, and a positive
`revision_retention_count` defaulting to `3`. Record a non-default retention
choice only as accepted deployment intent. Never copy revision UIDs, provider
endpoints, failed attempts, or live serving/cleanup state into a Blueprint, and
never place retention inside the automatic-redeployment tag policy. Backend
retention is asynchronous and may garbage-collect an unshared former runtime
image only through canonical image-dependency preflight.

## Preserve The Organization Environment Contract

Read `mainsequence://platform/skills/organization-environments` whenever a
design involves shared data or configuration, exact branch lanes, CodeRepository
Executors, environment management, or promotion between environments. That
skill owns the complete cross-platform ontology and lifecycle guidance; do not
reconstruct it from individual CodeRepository, MetaTable, Secret, or release rules.

When a CodeRepository Blueprint depends on this architecture, record the intended
exact branch lane and environment assumptions in decisions, constraints,
dependencies, and acceptance criteria. Do not add unapproved persisted fields
or a second environment permission system to the Blueprint. Do not add a new
top-level Blueprint environment domain without a separately approved contract.

## Choose The Interaction Mode

Use one Blueprint contract in both modes.

### Guided Mode

Default to guided mode when the user's experience is unknown.

- Ask one material architecture question at a time.
- Define each platform term before relying on it.
- Explain why each component is needed.
- Explain alternatives and why they were rejected.
- Explain grain, keys, relationships, constraints, dependencies, lifecycle,
  and failure consequences.
- Write detailed rationale and acceptance criteria into the Blueprint.

For a MetaTable, explain what one row means, why its keys express that grain,
why each relationship needs a foreign key or constraint, and which access
pattern justifies an index. State the physical database dialect because it
affects the SQLAlchemy types, defaults, and constraint behavior.

For a TimeIndexTableUpdater, explain the produced dataset, complete output grain,
input/output data frequency and freshness expectations, dependencies,
incremental boundary, determinism, and consumers. Do not assign executable
cadence to the updater.

### Advanced Mode

Use advanced mode when the user requests it or demonstrates the relevant
platform knowledge.

- Accept compact technical intent.
- State assumptions in batches.
- Focus on invariants, tradeoffs, risks, and architecture changes.
- Keep rationale concise but complete.
- Prefer a Blueprint diff when maintaining an existing design.

Never reduce architectural rigor in advanced mode.

## Start From Intent

Establish:

- the problem and CodeRepository boundary;
- users and consuming systems;
- outcomes and observable success criteria;
- business concepts and relationships;
- required data, computation, interfaces, and schedules;
- security, ownership, latency, and operating constraints.

Separate verified facts, assumptions, decisions, and open questions. Ask only
for information that materially changes the architecture or authorization
boundary.

Do not start from a list of platform records.

## Maintain The CodeRepository Ontology

Define CodeRepository concepts before mapping them to implementation components.

For each concept, record:

- a stable CodeRepository-local key;
- human-facing name;
- precise definition;
- business identity;
- source of truth;
- important attributes;
- lifecycle when the concept changes state.

For each relationship, record:

- subject, predicate, and object concept references;
- cardinality;
- required or optional participation;
- governing invariant;
- the components that materialize or enforce it.

Record invariants as testable statements. Do not use a database table name as
the definition of a business concept.

## Produce One Connected Blueprint

Produce or update one CodeRepository-owned, version-controlled
`code_repository_blueprint` YAML document with this top-level structure:

```yaml
code_repository_blueprint_version: "2"

code_repository:
  purpose: ...
  users: ...
  outcomes: ...
  success_criteria: ...

ontology:
  concepts: ...
  relationships: ...
  invariants: ...

decisions: ...
open_questions: ...

metatables: ...
time_index_table_updaters: ...
jobs: ...
apis: ...
cli: ...
code_repository_to_agent: ...
static_sites: ...
```

The exact repository path is CodeRepository policy until the platform approves one.
When no path is established, return the complete YAML for review instead of
inventing a location.

## Use Local References

Give every reusable Blueprint item a stable key. Reference it with its section:

```text
code_repository.outcomes.daily_portfolio_risk
ontology.concepts.portfolio
metatables.portfolios
time_index_table_updaters.calculate_daily_portfolio_risk
jobs.nightly_reconciliation
apis.portfolio_risk_api
cli.calculate_portfolio_risk
code_repository_to_agent.skills.portfolio_risk_analysis
```

These references exist only inside the Blueprint. They do not allocate a
backend record, replace a public UID, or create a platform registry.

Do not require platform UIDs for planned components. Never use numeric database
identifiers. Use a public UID only in a platform operation after verifying an
existing object through the canonical operation.

## Connect Every Component

For every MetaTable, TimeIndexTableUpdater, Job, API, CLI command, and static site, record:

- `key` and human-facing `name`;
- `purpose`;
- `rationale`;
- `fulfills` outcome references;
- `domain_concepts` references;
- typed dependencies;
- consumers;
- constraints;
- acceptance criteria;
- relevant decision references.

Reject orphan components that support no outcome or have no meaningful
consumer.

When a component requires process configuration, record the required variable
names, non-secret value intent, target ownership, and secret exclusions in its
existing constraints, decisions, dependencies, and acceptance criteria. The
implementation handoff uses the live `code-repository-workflows` API `2.3.0` template.
Do not add a second Blueprint environment-variable domain or represent a
workflow literal as a platform Secret/Constant resource.

`depends_on`, `consumers`, and acceptance criteria are Blueprint architecture
links. Do not misrepresent them as persisted fields on a MetaTable, TimeIndexTableUpdater,
Job, API, or CLI record.

## Design MetaTables

Use a MetaTable for a CodeRepository table whose shape is authored in SQLAlchemy or
for an existing physical table registered in the independent MetaTables application.

For a CodeRepository-owned table, SQLAlchemy metadata is the authored table shape.
The physical backend is one of SQLite, PostgreSQL, or TimescaleDB.
Record the dialect explicitly and keep the shape compatible with it. Do not
turn the Blueprint into Python code.

Record:

- relational or time-indexed table kind;
- physical database dialect: `sqlite`, `postgresql`, or `timescaledb`;
- application-owned management and schema-evolution intent, resolved through
  the independent MetaTables guidance;
- physical schema and unqualified SQLAlchemy table name;
- row grain in one precise sentence;
- business key;
- columns with SQLAlchemy/logical type, optional dialect-specific backend type,
  meaning, nullability, default behavior, and concept;
- primary and unique constraints;
- foreign keys with their ontology relationship and rationale;
- indexes with the lookup, join, ordering, or uniqueness need that justifies
  them;
- producers and consumers.

Do not add a foreign key, index, or constraint without explaining its semantic
or access-pattern purpose. Do not confuse an index with a business invariant.
For application-owned schema evolution, SQLAlchemy/Alembic owns physical DDL;
MetaTable owns catalog identity, permissions, physical-table binding, and
introspected metadata.

## Design TimeIndexTableUpdaters

Use a TimeIndexTableUpdater for deterministic computation that incrementally produces or
maintains `TimeIndexMetaTable` data.

Record:

- the output `TimeIndexMetaTable` reference (stored in the Blueprint's existing
  `output_metatable` field);
- complete output grain: time index plus all identity dimensions;
- input/output data frequency and freshness expectation, without treating it as
  executable cadence;
- TimeIndexTableUpdater, MetaTable, and external-data dependencies;
- update boundary and partitioning;
- determinism and idempotency expectations;
- backfill and replay behavior;
- lineage and downstream consumers.

Require the TimeIndexTableUpdater output grain to agree with its output
`TimeIndexMetaTable`. Keep storage shape in the table resource and update
behavior in the TimeIndexTableUpdater.

## Design Jobs

Use a Job for CodeRepository code that should run on demand or on an optional
interval/crontab schedule and does not belong in deterministic TimeIndexTableUpdater
production or a request-time API.

Record:

- `name`;
- one repository-relative `execution_path` for a `.py` or `.yaml`
  CodeRepository file;
- the initial image rule: Django derives the exact image and full commit from
  the immutable repository workflow event;
- whether future qualifying repository events may promote the Job to another
  exact image, plus the optional exact-tag regex policy;
- `cpu_request` and `memory_request`;
- optional `gpu_request` and `gpu_type`;
- `spot`;
- positive `max_runtime_seconds`;
- optional `scheduled_command_args`, with one exact string per argument for
  future scheduler-created JobRuns;
- optional `task_schedule` using the existing interval or crontab schedule
  shape, including start-time or one-off intent when needed;
- for every crontab, the canonical IANA timezone in which its wall-clock fields
  are evaluated; interval schedules have no timezone.

Do not translate a calendar schedule to the designer's current UTC offset. The
Job snapshots its chosen timezone and does not follow later Command Center
preference changes. Treat an omitted legacy timezone as UTC-compatible but not
as confirmed user intent; new designs should always state the zone.

When scheduled execution needs arguments, record an exact ordered
`scheduled_command_args` list. Do not collapse it into a shell string or trim,
deduplicate, split, or otherwise normalize its items. The backend snapshots the
current Job list into each future scheduler-created JobRun. Manual Job starts
remain independent: omission still means an empty per-run list and an explicit
manual `command_args` list is never merged with the scheduled configuration.

The canonical creation flow infers the Job type from `execution_path`. Do not
declare an independent type or command contract in the Blueprint. A `.ipynb`
file may remain a browsable CodeRepository resource, but it is not an
executable Job target and must not be converted or declared with a retired
`notebook` discriminator. The retired app target and `app` discriminator must
not be used.

The lean-Python ABI migration is backend-owned and may place Python image
builds and deployments under a temporary maintenance lock. A Blueprint must
not encode a fallback image, legacy Jupyter path, alternate launcher, or
cutover-specific retry branch.

Standalone Jobs are created only from workflow declarations under
`.mainsequence/workflows/`. They carry no image or commit selectors: workflow
API `2.3.0` derives the exact image from the immutable repository event.
The backend never resolves branch HEAD at runtime or persists an image-less
Job. Existing Jobs may still be run manually.

Explain why the workload is a Job rather than a TimeIndexTableUpdater, API request, or local
developer command.

Do not invent Job fields for retry policy, failure policy, queues, dependency
graphs, output schemas, or completion callbacks. A Job invocation creates a
JobRun whose existing runtime status is observed separately. The Blueprint's
cross-component references and acceptance criteria do not become Job model
fields.

When implementation verification or diagnosis needs execution evidence, hand
off to `mainsequence://platform/skills/log-exploration`: use the exact JobRun
tool when its UID is known or bounded `job_run.search_logs` in one exact
Organization Environment. Log filters, cursors, retention state, and returned
rows are operational evidence and never Blueprint fields.

## Design APIs

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

Use an API as a typed CodeRepository interface over accepted business behavior and
data.

Record:

- intended consumers;
- reads-from and writes-to references;
- operations with purpose, method/path intent, request contract, response
  contract, and read/mutation classification;
- authentication and authorization expectations;
- latency and availability expectations;
- error behavior;
- deployment/release expectation;
- acceptance criteria.

When one FastAPI application serves both HTTP/REST and WebSocket routes, keep
them in one `apis[]` component unless the accepted architecture requires an
independent lifecycle, scaling, security, or failure-isolation boundary. In
the existing operations/routes contract:

- describe HTTP method, path, request, response, and side effects for REST;
- describe WebSocket path/upgrade intent, browser versus non-browser consumer,
  handshake authentication, client/server message shapes and direction,
  application subprotocols, close/error behavior, and reconnect expectations;
- record one shared FastAPI release expectation and acceptance tests for both
  transports.

For an external OAuth redirect or webhook, record each exact `GET` or `POST`
path in `apis[]` and identify it as provider-facing public ingress. Record
the provider-side state/signature and replay checks as application acceptance
criteria. Hand only those method/path pairs to the existing `kind: fastapi`
workflow `public_ingress` declaration. Registration uses the backend-issued
`public_url` after `effective_public_ingress` confirms the route on a ready
active revision; adding a path requires deployment and removal revokes it
immediately. Do not place provider secrets or generated release URLs in the
Blueprint or workflow.

Design the application's business operations and readiness dependencies; the
platform launcher automatically reserves and serves
`GET /ms-health-deployment` for ordinary FastAPI and Harness Agent runtimes.
Keep that platform endpoint outside `apis[]`, workflow fields, and Blueprint
capabilities. When the runtime truly cannot serve without a narrowly scoped
dependency check, record that requirement for implementation through the
SDK/launcher's optional typed readiness hook.

For browser or API consumers of a platform-deployed FastAPI release, include
an acceptance criterion that the client resolves ResourceRelease runtime
access through Django, waits on `waking` with bounded backoff, and sends the
business request only with the credential returned by a ready response. Local
and debug execution instead waits on its locally owned process; Django does
not control that lifecycle.

If an API operation must call Django on behalf of the inbound FastAPI caller,
record that actor requirement and the operation-specific authorization rule.
The implementation must present both the verified signed caller assertion and
the revision-bound workload credential. Neither `X-User-UID` nor the workload
credential's workload User is caller proof. Install the SDK request-identity
integration once; application policy uses the verified requester when present,
otherwise the authenticated person or workload. ADR-0046 requires coordinated rollout;
do not claim actor-aware behavior is available until gateway forwarding and
runtime verification are deployed. Service-only operations use workload
identity without manufacturing an inbound actor.

Harness Agents also verify signed request identity through their installed
runtime SDK. They use the Harness Agent product adapter in `coding-agents`;
ordinary FastAPI releases use the FastAPI adapter in `fapi`. FastAPI business
routes, CORS and WebSocket configuration do not configure a Harness Agent.

Do not add a WebSocket Blueprint domain, release kind, deployment target,
workflow resource, or parallel application merely because one route upgrades
the connection.

Do not rebuild producer logic in an API. Reference the TimeIndexTableUpdater or MetaTable
that owns the data.

When implementation produces an ordinary FastAPI or Static Site target, hand
the accepted release intent to the `resource-release` execution skill. For a
Harness Agent, hand the accepted intent to `code-repository-workflows`; its
`kind: harness_agent` adapter is the only creation path. Do not copy the live
ResourceRelease serializer into the Blueprint.

API acceptance criteria may require observable runtime behavior, but
implementation diagnosis belongs to
`mainsequence://platform/skills/log-exploration`. Use bounded
`resource_release.search_logs` or the exact release/deployment-run log tool;
do not copy the public log query vocabulary, cursors, or transient rows into
the Blueprint.

Managed Streamlit deployment is not a supported implementation handoff. Record
the requirement as unresolved until the design chooses a supported target.

For a browser-called FastAPI, record the intended exact or wildcard browser
origins as API deployment intent when the platform default is not sufficient.
Omitted FastAPI creation uses the current platform deployment's static-site
wildcard (`site-dev` in development and `site` in production); this is not an
Organization Environment mapping. The execution handoff uses the FastAPI
release's canonical persisted `cors_allowed_origins`; CodeRepository code and workflow
`env_vars` do not install or configure platform CORS middleware. A changed
policy takes effect immediately for Django's trusted preflight after the
infrastructure #41 gateway cutover; application-response CORS headers still
require runtime redeployment. See the ResourceRelease reference contract.

When an accepted static site calls an accepted FastAPI release through
platform delegation, record the exact source StaticSiteRelease UID, exact
target FastAPI ResourceRelease UID, required target CORS origin policy, and
same-Organization requirement in the connected API/static-site design and
implementation handoff. Do not infer or require a common CodeRepositoryBranch,
repository branch, or OrganizationEnvironment. These UIDs are deployed
release identities, not a new persistent Blueprint relationship model; if the
releases do not yet exist, make their later UID resolution an explicit handoff
condition rather than inventing values.

The frontend asks Command Center for delegated access to the stable target
release URL; backend routing selects only its ready `active_revision`. A
failed desired API revision leaves the previous active revision serving, and a
successful promotion does not rebuild the static site. Do not invent browser
release-identity configuration, provider URLs, dependency registries, logical
binding keys, or additional authorization.

If the accepted runtime release is implemented as an automatically managed
repository workflow, also use `code-repository-workflows`: record source and promotion
intent without any platform UID. Django Pod Manager owns exact-commit indexed
resource resolution, image selection/building, DeploymentRuns, revisions, and
the product deployment adapter.

For that combined FastAPI application, hand off exactly one workflow
`fastapi` declaration whose `source_path` loads the FastAPI instance containing
both route types. Do not declare `resource_release`, `release_kind`,
`resource_uid`, `related_image_uid`, or another platform UID.
`automatic_redeployment` promotes the complete exact-commit application; it
does not enable WebSockets. The standard FastAPI runtime always supports the
transport and creates connection state only for an incoming upgrade attempt.
Do not add `websocket_enabled`, Uvicorn adapter, ping, ticket, gateway, or
second-resource fields. Django, the Pod Deployment Orchestrator, and
infrastructure own connection policy and gateway rollout under ADR-059.

## Design The CodeRepository CLI

Use `cli` to define the CodeRepository's executable human-, automation-, and
agent-facing command surface.

For each command, record:

- an exact command path;
- purpose and rationale;
- the components it reads, writes, invokes, or inspects;
- typed inputs with meaning, requiredness, and validation;
- machine-readable output contract;
- `read` or `mutation` side effects;
- authorization and preconditions;
- failure and retry behavior;
- examples and acceptance criteria.

The command must map to real CodeRepository behavior. Do not place platform permission
policy only in the CLI.

## Design CodeRepository To Agent

Use `code_repository_to_agent` only when the CodeRepository itself should become a
code-repository-backed agent.

Record:

- whether it is enabled;
- a human-facing role name, purpose, and rationale;
- explicit boundaries;
- CodeRepository-agent skills;
- optional accepted deployment intent using `automatic_deployment` and the
  nested `automatic_redeployment_policy.tag_regex`; omit it or use null to
  deploy every push, or record a regex when only the repository's own release
  tags (for example created by CI) should deploy; and
- the project ASGI resource selected for the Harness Agent runtime.

Accepted Harness Agent deployment intent may also record `min_scale`,
`max_scale` and their availability, throughput and cost rationale within the
existing `code_repository_to_agent` deployment block; implementation hands the
values to `spec.min_scale` and `spec.max_scale`.

The deployment handoff is exactly one workflow resource with
`kind: harness_agent`. `harness_agent` is also the persisted release kind and
DeploymentRun target kind. Do not declare FastAPI, `release_kind`, `agent_uid`,
`resource_uid`, `related_image_uid`, `agent_type`, a harness selector, or a
special runtime image. Declare the repository-native `source_path` plus the
required `llm_provider`, `llm_model`, and `llm_thinking` runtime defaults.
Django validates those defaults, creates the Agent identity only after the
exact-commit repository source card has been indexed, persists the defaults,
then binds it to the standard project release. The source card must provide the
Agent's non-empty human-facing name and description.

For every CodeRepository-agent skill, record:

- key, name, factual description, and rationale;
- one or more verified executable repository references;
- when-to-use guidance and workflow;
- inputs, outputs, constraints, examples, and acceptance criteria.

Require every skill to reference at least one implemented command or structured
repository operation. A skill may compose several executable references into a
user workflow, but it must not duplicate the executable contract, hide a
mutation, or invent CodeRepository behavior. Runtime-specific tool declaration
and extension paths belong to that runtime library's version-matched skills.

Use the separate `code-repository-to-agent` platform skill to prepare repository
instructions, CodeRepository-owned skill files, and the source card after the
Blueprint is accepted and the referenced executable behavior exists.

Harness Agent deployment intent never includes a caller-built special runtime
image or any persisted platform UID. Agents owns semantic identity, card, and
provider/model/thinking defaults. Django Pod Manager owns the bound release,
image, DeploymentRun, revision, and Knative deployment. A CodeRepositoryBranch
can own only one Harness Agent across all workflow files.

## Design Static Sites

Use `static_sites` when the CodeRepository needs a browser frontend deployed through a
static ResourceRelease. Keep the item connected to CodeRepository outcomes, domain
concepts, consumers, and accepted APIs. Do not turn the Blueprint into a
route-by-route UI specification, a frontend scaffold, a Command Center SDK
contract, or a duplicate ResourceRelease request.

Record:

- a stable key, human-facing name, purpose, and rationale;
- `fulfills` CodeRepository-outcome references;
- `domain_concepts`, `depends_on`, `consumers`, `constraints`, and
  `decision_refs` from the common component contract;
- `deployment.root_directory`, `deployment.routing_mode`, and
  `deployment.automatic_deployment` only when those deployment choices are
  already accepted;
- optional `deployment.automatic_redeployment_policy.tag_regex` only when a
  tag-gated promotion rule is accepted; omit it or use null to deploy every
  push. The platform renders no tag names; the repository creates its own
  release tags, for example in CI;
- optional `deployment.revision_retention_count` only when a positive
  non-default rollback-history decision is accepted; and
- observable acceptance criteria.

When an accepted Static Site must appear in Command Center navigation, record
the intended label, required allowlisted fallback icon key, optional
repository-backed monochrome mask intent, enabled state, and recipient category in
that Static Site's constraints and acceptance criteria. The implementation
handoff uses the workflow's nested `navigation_link`; it does not add a
top-level Blueprint links domain. Record that the Organization automation
identity applying the workflow authorizes placement, so anyone who can push to
the exact CodeRepositoryBranch can place the link for the audiences the file
names. The pusher's identity is recorded for audit only. Do not claim placement
grants target access.

When repository-backed mask intent is accepted, the implementation handoff
uses workflow APIs `2.2.0` and `2.3.0` `navigation_link.icon_mask_path`; the Blueprint does
not copy the path as a deployment field. The only supported asset is at most
512 KiB and is either a sanitized basic-geometry SVG with a finite positive
square `viewBox`, or a static square transparent PNG/WebP from 32 x 32 through
512 x 512 pixels inclusive. Scripts, text, style, external references,
embedded data, JPEG, animation, and opaque rasters are rejected. The path is a forward-slash,
repository-root-relative POSIX path of at most 1024 UTF-8 bytes, contains no
empty, `.`, `..`, `.git`, backslash, NUL, or symbolic-link component, and ends
at a regular file in the exact event-commit checkout. Filename extensions and
media types do not substitute for byte validation. Omission preserves the
stored mask, explicit null removes it, identical sanitized bytes are a digest no-op,
and `icon_key` remains the required fallback. Missing or invalid mask content
warns without blocking Static Site deployment and preserves prior navigation
state; unsafe path syntax is a blocking workflow validation error. A later
valid push or redeployment re-resolves and restores the mask.

If a static site calls a FastAPI release through platform delegation, its
constraints must state that implementation resolves the exact
source StaticSiteRelease UID and target FastAPI ResourceRelease UID, configures
the target CORS policy for the source origin, and preserves same-Organization
ownership. Do not add a CodeRepositoryBranch or OrganizationEnvironment
co-location constraint. The installed Command Center SDK skills own frontend
credential transport; the Blueprint must not contain tokens or reproduce that
protocol.

Do not put API URLs, environment values, tokens, credentials, provider state,
framework versions, Node versions, output defaults, or a copy of the
ResourceRelease serializer in the Blueprint. The complete installed Command
Center SDK skill bundle owns frontend implementation. The MCP `static-site`
skill reads the canonical live capabilities; the `resource-release` skill
directs creation through repository workflows and owns configuration,
deployment, and deployment-state operations.

Use this compact shape:

```yaml
static_sites:
  portfolio_console:
    name: Portfolio Console
    purpose: Provide the browser interface for portfolio analysis.
    rationale: Users need an interactive presentation over the accepted API.
    fulfills:
      - code_repository.outcomes.interactive_portfolio_analysis
    domain_concepts:
      - ontology.concepts.portfolio
    consumers:
      - code_repository.users.portfolio_manager
    constraints:
      - Must be usable from the supported Command Center application surface.
    decision_refs:
      - decisions.browser_frontend
    deployment:
      routing_mode: spa
      automatic_deployment: true
      automatic_redeployment_policy:
        tag_regex: null
    acceptance_criteria:
      - The supported production build succeeds.
      - Portfolio analysis is usable by the intended browser consumers.
```

## Validate The Blueprint

Before handoff, verify:

- all keys are unique within their scopes;
- every local reference resolves;
- every component fulfills an outcome;
- every referenced ontology concept exists;
- every relationship names valid concepts;
- every MetaTable has explicit grain and keys;
- foreign keys cite compatible target keys and ontology relationships;
- indexes cite concrete access patterns;
- every TimeIndexTableUpdater output and grain agree with its MetaTable;
- API data dependencies resolve;
- a combined HTTP/WebSocket FastAPI API has one release/deployment expectation
  unless an independent isolation decision is recorded;
- every CLI command maps to real components and declares side effects;
- every CodeRepository-agent skill references at least one compatible
  executable repository operation;
- every static-site consumer reference resolves;
- every delegated static-site-to-FastAPI composition records exact deployed
  source and target release identity resolution, whether the platform CORS
  default or a custom override is intended, and
  same-Organization ownership without inventing an environment co-location
  rule;
- every organization-environment assumption uses an Organization-owned
  environment and backend-resolved CodeRepositoryBranch assignment;
- every proposed shared environment requires the same exact repository branch
  name across all participating CodeRepositoryBranches;
- static-site deployment intent contains only currently approved canonical
  release fields;
- automatic redeployment intent is target-specific, uses only the nested
  `tag_regex`, and does not invent a trigger mode or client-side evaluator;
- no static-site item duplicates frontend implementation or Command Center SDK
  contracts;
- no secret, credential, provider location, numeric database ID, or transient
  run state appears.

Report validation errors against exact Blueprint paths. Do not silently repair
an accepted architectural decision.

## Maintain Rather Than Rebuild

For an existing Blueprint:

1. Read the current Blueprint and relevant platform/repository evidence.
2. Identify drift between accepted intent and verified implementation.
3. Separate architecture drift from ordinary implementation defects.
4. Propose the smallest coherent Blueprint change.
5. Preserve stable keys unless the concept itself is replaced.
6. Update affected decisions, references, and acceptance criteria together.
7. Hand only accepted changes to execution skills.

Git owns document history. Do not embed a second change ledger in the
Blueprint.

## Verify Platform State

Use approved platform reads to verify existing objects, permissions, and state.
Treat not-found as non-disclosure when the canonical operation does so.

The concrete `code_repository.create` tool accepts canonical DRF CodeRepository-creation
fields. It does not accept natural-language intent or a CodeRepository Blueprint.
Resolve intent first, then call the typed operation only when action is
requested.

Do not send `repository_branch` to `code_repository.create`; the server creates `main`.
GitHubRepositoryBinding branch discovery is not an MCP tool in the current catalog, and
manual branch creation/import is retired by the ADR-031/ADR-0036 lifecycle.
After bootstrap, only a signed provider push may create a missing CodeRepositoryBranch,
and only when the Organization already owns the exact matching environment.
Git does not create that environment or choose application storage. No MCP branch
creation/import tool exists. Canonical DRF repository detail returns the owning
logical CodeRepository UID; it never computes a branch UID.

This Git-driven lifecycle is deployed. A persisted signed push creates a
missing CodeRepositoryBranch only when the Organization already owns the exact
matching Environment. Otherwise the push is ignored and creates no branch. Do
not design manual branch creation/import as an alternative lifecycle.

Branch removal is scoped to one exact Organization Environment and requires
edit authority on the parent CodeRepository. It always preserves the provider
repository and provider Git branches. When a detail or bulk selection removes
the final remaining CodeRepositoryBranch, Django atomically removes the
exhausted logical CodeRepository and its local GitHubRepositoryBinding registry
row as lifecycle cleanup under that branch operation. Directly selecting a
logical CodeRepository for deletion remains a separate Organization-admin
operation.

Choose the public `code_repository_type` deliberately when the design establishes the
primary CodeRepository scaffold: `python` or `vite_react`. The immutable value belongs
to the logical CodeRepository, and every CodeRepositoryBranch under it uses that one type;
never design mixed branch types or a branch-level override. The public
CodeRepositoryBranch list/detail contract projects the parent's value read-only for
type-aware presentation; that projection is not branch storage, write authority, or a
fallback. Omission during
CodeRepository creation means `python`. Do not invent separate language, framework,
profile, or scaffold version fields. The canonical CodeRepository response exposes the derived technology, the
mandatory pinned framework image, and repository/commit-scoped SDK
observations. A Vite CodeRepository keeps browser build variables on its
StaticSiteRelease. MetaTables owns its storage registry independently of platform
Environments and repository branches. CodeRepository creation accepts no storage
selector. The platform resolves its production Environment for `main` and may derive
the submitted bootstrap Environment's branch; these relations serve ordinary platform
operations such as Secrets and Constants. Branch responses contain no storage routing.
Do not infer framework-image paths, tags, or runtime versions: the physical
infrastructure producer advertises those values, and CodeRepository creation resolves
its advertised default when no image UID is supplied.

Never claim that a mutation succeeded until the canonical response confirms
it. After an ambiguous result, retrieve or search before deciding whether to
retry. For CodeRepository bootstrap, stop polling on `FAILED`; MCP exposes no
provisioning retry mutation.

When a newly created or existing CodeRepository must become a local checkout, hand
that separate lifecycle step to the `code-repository-local-setup` platform skill. That
skill waits for repository initialization, registers only a caller-generated
public deploy key, and defines the host-managed clone and authentication
handoff. CodeRepository design never handles local paths, SSH private keys, or
credential values.

## Handoff

Return:

1. the complete or updated Blueprint;
2. the interaction mode used;
3. verified facts and evidence;
4. assumptions and open questions;
5. architectural decisions and consequences;
6. validation results;
7. the execution skill responsible for each accepted component.

Keep logical architecture separate from implementation. Do not include Python
imports, dependency pins, virtual-environment commands, local absolute paths,
or generated credentials.

## Stop Conditions

Stop and ask for direction when:

- two materially different architectures satisfy the intent;
- a required ownership or authorization decision is missing;
- platform evidence contradicts a user assumption;
- a required concept has no approved Blueprint contract;
- the requested operation is not exposed through an approved interface;
- implementation would start before the Blueprint decision is accepted.

## Workflow Environment applicability

Optional root `scope.environments` is a non-empty list of unique exact
Environment names. Omission applies in the event branch's existing Environment.
`production` matches its backend-owned production role; other names match
case-sensitively. This is a file applicability filter, never Environment
selection. A nonmatching file is structurally validated but skips source/target
resolution, reconciliation and automatic deployment. It declares nothing on
that branch, so the branch's targets bound to it are deleted. Validation reports `applicable` for scoped
files. Generic deployment handlers honor the same exact-event result.

## Jobs around deployment

For a release requiring prerequisite or post-deployment work, keep the components
in the existing `jobs`, `apis`, `code_repository_to_agent`, or `static_sites`
Blueprint domains. Record invocation order, parallel work, exact candidate-image
sharing, runtime limits and failure expectations in their existing relationships
and deployment expectations. Handoff to code-repository-workflows for optional
`execution.steps` with prepare_image/run_job/deploy, local needs and image_from.
Django creates/reuses Jobs from their declarations. No manual Job creation or
UID wiring is required. A schema migration is one example of a general Job.
All targets in a connected group must admit the event under their promotion
policies. Scope is an optional Environment filter, not a new deployment target.
Post-deployment failure does not roll back an already active release. Script
retry, cross-file dependencies, matrices and outputs are outside this contract.

### Recorded model calls in application code

For a direct model call such as document extraction, application code can use
`mainsequence.client.InferenceClient` with the existing provider catalog and
stored credentials. It records requests, outputs and usage without an Agent or
AgentSession. The caller supplies full context and a stable idempotency key;
Main Sequence history remains until explicitly deleted. Provider-native options
and optional thinking/storage controls affect the selected upstream call.
Record this as an SDK dependency of the existing API, Job or CLI component;
it adds no Blueprint domain, MCP execution tool, or deployment. Specify who owns
the conversation, the Environment, the exact model requirement, and retry-key
handling. Runtime authentication supplies the workload and Environment;
verified delegation selects the effective User for authorization. Human calls
select an authorized Environment explicitly. Use a configured provider that
User can access, never the workload creator as a credential fallback.


## FastAPI MCP capability

A FastAPI API may expose an application-owned authenticated `/mcp` alongside its
HTTP routes. Workflow API `2.3.0` accepts `spec.mcp_enabled` (boolean, default
false). The flag is desired release configuration, fingerprinted and snapshotted
in immutable revisions; only the ready active revision advertises a connection.
Workflow omission resets desired state; PATCH omission preserves it.
`resource_release.mcp_endpoints` lists the FastAPI MCP endpoints you can see
with their `mcp_connection`. `resource_release.list` accepts the same
`mcp_available=true` filter and returns nullable `mcp_connection` metadata, also
present on detail. A cold runtime may have an
active connection; this is not a live-health guarantee.

Call this project endpoint **FastAPI MCP** and the central platform endpoint
**Django MCP**. Direct clients use the FastAPI MCP URL and shared Django OAuth
login, without Command Center or a preconfigured Django MCP. Existing access and
refresh JWT types are bound to that project resource; they do not authorize
central platform operations. Discovery does not install client configuration.

The application owns tools, business authorization and FastAPI/MCP lifespan;
shared REST/MCP/harness functions are optional project design. No registry,
generated REST adapter, Tau adapter or separate runtime is required. The SDK's
MCP hosting/context integration is owned by its existing ADR 0038, with caller
identity owned by ADR 0036; both are linked from platform ADR-0050. Verify launcher compatibility, live gateway challenges and
client startup behavior before enabling a project; backend support alone does
not prove that the deployed application has the mount.

Record acceptance checks for the supported stateless POST/JSON profile,
mounted-tool authorization with verified identity, composed application/MCP
lifespans, permitted browser Origins and bounded cold-start behavior. A business
mutation must not be replayed just because its response is uncertain. Hand off
client discovery, consent and connection steps to `resource-release`; no new
Blueprint keys or public-ingress exception are needed.

For a Harness Agent consumer, hand off explicit workflow
`spec.mcp_applications: [{resource: orders}]`, or entries with `repository` and
`resource` for another repository in the same Environment. The resource key is
the tool prefix, matches `^[a-z][a-z0-9_]{0,39}$`, is unique per Agent and cannot
be `mainsequence`. The target must declare `mcp_enabled: true`. Configure no UID
or URL. Bootstrap returns the resolved release UID and prefix; Tau registers
`<name>__list_tools` and `<name>__call_tool` and resolves access as its authenticated User or verified requester
per call. Registration grants no permission and does not limit other
platform-client calls. Record these consumers in existing API/deployment
handoff fields; no Blueprint field or version changes.

## Requester-bound access

An Organization admin may enable a Harness Agent to act for the person it serves.
Account for ordinary reads and writes: create, update, run, share and delete
use that person's ordinary permissions, including admin roles and the runtime Environment boundary.
No separate Agent tool approval or operation exclusion applies. Without delegation
a workload uses its own permissions; invalid delegation is rejected.
A prompt-injected Agent can cause persistent changes within those rights while
its request is active, for at most 24 hours from the original request. Use the
requester policy and shared statement in `code-repository-workflows`; do not put
person IDs or private lease proofs into repository deployment declarations.
This changes authorization, not Blueprint fields or deployment ownership.
