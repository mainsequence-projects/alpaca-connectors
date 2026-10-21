---
name: code-repository-workflows
description: Create and validate backend-managed API 3.0.0 deployment declarations and optional Job dependencies under .mainsequence/workflows for Jobs, FastAPI applications, Static Sites, and the one branch-owned Harness Agent.
---

# Main Sequence CodeRepository Workflows

Repository workflow files are repository-authored deployment intent. Django owns
parsing, validation, permissions, exact-commit resolution, image orchestration,
deployment, and reconciliation. Clients must not implement another parser or
construct a second interpreted deployment payload.

## Deployment Ownership

Django Pod Manager is the deployer. Repository authors declare source paths,
runtime configuration, compute intent, and promotion policy. Django resolves
the exact-commit indexed source, selects or builds the code repository image, creates
or reuses the durable target, creates DeploymentRuns and revisions, and calls
the product deployment adapter. Agents owns the Harness Agent's semantic Agent
record and its provider/model/thinking defaults; Pod Manager owns its bound
ResourceRelease and deployment lifecycle.

Workflow YAML never supplies a Job, source-resource, image, branch, cluster,
Agent, ResourceRelease, revision, or DeploymentRun UID. Those identifiers are
backend-generated results used only after reconciliation.

Harness Agents may declare optional `spec.mcp_applications`, for example
`[{resource: orders}, {repository: Inventory Service, resource: inventory}]`.
Omitting `repository` targets a resource in this file; specifying it targets a
unique workflow resource in that repository in the same Environment. Targets
must be FastAPI with `mcp_enabled: true`. Resource keys are tool prefixes:
unique per Agent, matching `^[a-z][a-z0-9_]{0,39}$`, never `mainsequence`.
No UID, URL or alias is accepted. Validate before pushing. Same-file dependencies
are applied first; failed targets cannot register an old release. Removing the
field clears the workflow-owned registration at the next bootstrap.
Tau bootstrap returns names and resolved release UIDs, and Tau resolves current
access as its authenticated User or verified requester per MCP call. Registration grants no permission and does
not restrict other platform-client calls. Invalid references fail session load.

## Procedure

1. Identify the exact `CodeRepositoryBranch` public UID.
2. GET `/api/v1/code-repository-branches/{uid}/workflow-template/`.
3. Edit the returned YAML using its advertised `api_version`.
4. POST `path` and `content` to
   `/api/v1/code-repository-branches/{uid}/validate-workflow/`.
5. Fix every validation error before committing.
6. Save the file as a direct `.yaml` or `.yml` child of
   `.mainsequence/workflows/` and commit it.
7. Inspect the repository-event action and resulting DeploymentRuns. A Git
   commit alone does not prove that deployment succeeded.

Repository processing is branch-specific. The event repository, exact branch
ref, persisted CodeRepositoryBranch, and full pushed commit must agree. Image
builds consume one backend-materialized, checksummed archive for that commit;
providers do not select branch HEAD. Automatically managed targets receive only
the verified digest-pinned code repository image selected by the backend.

## File Contract

For accepted idle-latency requirements, record the desired replica floor and
its resource-cost rationale in existing deployment expectations; for accepted
throughput requirements, record the desired replica ceiling and the per-replica
capacity it assumes. Workflow API `3.0.0` accepts `min_scale` (`0`
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
serves at once. Workflow API `3.0.0` also accepts, for FastAPI and
Harness Agents, `container_concurrency` (`1` through `1000`, the hard limit of
requests in flight per replica), `concurrency_target` (`1` through `1000` and at
most `container_concurrency`, the per-replica autoscaling target) and
`timeout_seconds` (`1` through `600`). Each is optional: omitted on creation it
keeps no limit, Knative's default target and the platform's 300-second
timeout; on update omission preserves it and explicit null resets it. An open
WebSocket counts as one request for its whole lifetime, so a FastAPI release
that serves WebSockets sizes `container_concurrency` for its connections too.
Total capacity is about `max_scale` x `container_concurrency`.

- Every file requires `api_version`, `name`, and `resources`.
- `3.0.0` is the only supported version; a file of any other version is
  refused before anything of it is applied, so a repository switches its
  files to `3.0.0` before its next push. It accepts `job`, `fastapi`,
  `static_site`, and `harness_agent`. To switch a `2.3.0` file, set
  `api_version: "3.0.0"` and replace each `access` level with the policy it
  gave (see Declared Access).
- Older supported versions accept only their backend-advertised kinds. They do
  not accept `harness_agent`.
- Every resource requires a stable `key`, supported `kind`, and typed `spec`.
  A `job`, `fastapi` or `harness_agent` resource may also declare its
  workload's `access` (see Declared Access).
- A declaration owns its target. On the next push of the branch head, these
  delete the target:
  - removing the declaration or its file;
  - a `scope` that excludes the branch's Environment.

  Renaming a file or key deletes the old target and creates a new one, with new
  UIDs and URLs. Whatever holds the old UID breaks: a static site whose
  `build_environment` pins a FastAPI release UID gets 404 until that value is
  updated and the site rebuilt. Before renaming or removing a FastAPI
  declaration, find the static sites that call it. A workflow static site that
  names the release in `build_environment` (below) is rebuilt with the new UID. An invalid file deletes nothing. The repository-event result
  lists every evaluated target under `deletions`.
- Only direct `.yaml` and `.yml` children are processed.
- Validation is read-only and uses the same validator as event application.
- Unknown fields are errors. Do not use `scheduled_jobs.yaml`.

Target-owned non-secret `env_vars` use this shape:

```yaml
env_vars:
  - name: FEATURE_MODE
    value: conservative
```

Omission preserves existing values, an empty list clears them, and a present
list replaces them. Static sites use `build_environment` instead. Reserved
platform credential, runtime, deployment-control, and transport variables are
rejected.

## Harness Agent

Use exactly one `harness_agent` declaration when the branch's project should
run a repository-backed coding harness.

`harness_agent` is the single concept throughout the deployment contract:

- public workflow kind: `harness_agent`;
- persisted ResourceRelease kind: `harness_agent`;
- DeploymentRun target kind: `harness_agent`;
- runtime label and observability kind: `harness_agent`.

The user does not declare FastAPI. Django uses the Harness Agent product
adapter and existing `coding-agents` namespace; only the product-neutral
Knative builder is shared with FastAPI. The user also does not supply an Agent
identity. Django creates the `Agent`, derives its Organization Environment from
the branch, creates the `harness_agent` release, binds both, and returns
`agent_uid` and `resource_release_uid` as results.

The Agent exists while any workflow file of the branch declares a
`harness_agent`. Moving the declaration to another file, or renaming its key,
keeps the same Agent and release. Removing it deletes the release and the
Agent, with the Agent's sessions.

Agent creation requires the indexed `.agents/agent_card.json` at the exact
repository-event commit. Its non-empty top-level `name` and `description`
become the live Agent presentation fields. Missing or malformed cards reject
reconciliation before an Agent exists; repository and branch metadata are not
presentation fallbacks.

Example:

```yaml
api_version: "3.0.0"
name: repository-runtime
resources:
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
      cpu_request: "1"
      memory_request: 2Gi
      spot: false
      env_vars:
        - name: FEATURE_MODE
          value: conservative
```

Supported `harness_agent.spec` fields are:

- `source_path`;
- `llm_provider`, `llm_model`, and `llm_thinking`;
- `automatic_deployment`;
- `automatic_redeployment`;
- `revision_retention_count`;
- `cpu_request`, `memory_request`, `gpu_request`, `gpu_type`, and `spot`;
- `command_center_icon`; and
- `env_vars`.

`command_center_icon` sources the Agent's Command Center icon from the same
repository. It is `null` or `{path, rendering}` with optional
`image_rendering`: `path` is a safe repository-relative POSIX path to an SVG,
PNG or WebP file of at most 64 KiB, square and at most 128 px per side (clients
draw it at 20 to 32 px and embed it; a bigger file is refused with its size),
and `rendering` is `mask` (monochrome,
follows the theme color) or `color` (keeps the logo's colors). Never guess
`rendering`. A `color` icon may be an animated PNG (APNG): at most 120 frames,
each at least 20 ms, square 32 to 128 px; frame 0 is the still shown in lists,
so make it the rest pose. For pixel art add `image_rendering: pixelated`
(only with `color`) so it scales with sharp pixels. Omitting the field keeps
the current icon and `null` removes it. A malformed object or unsafe path
invalidates the file; a missing or invalid file only returns an `agent_icon_*`
warning and the Agent still deploys. Keep the icon file beside, never inside,
`.agents/agent_card.json`:

```yaml
    command_center_icon:
      path: .agents/command-center-icon.svg
      rendering: color
```

`command_center_animations` adds pose sprites and tool rules: `poses` maps up
to 12 names (`^[a-z][a-z0-9_]{0,31}$`, never `idle`) to exactly `{path}`, drawn
like the icon and under the same 128 px and 64 KiB limits per pose file; `tools` lists `{application, pose}` where `application` is a
`resource` key of this spec's `mcp_applications`. Command Center shows the
rule's pose while that application's tools run, `working` for other tools,
`thinking` while the agent thinks and `error` after a failed tool. Omission
keeps them, `null` removes them, and a missing pose file only warns:

```yaml
    command_center_animations:
      poses:
        working: {path: .agents/sprites/working.png}
        reading: {path: .agents/sprites/reading.png}
      tools:
        - application: tutorial_api
          pose: reading
```

Never put any of the following in that spec:

- `uid`;
- `resource_uid`;
- `related_image_uid`;
- `agent_uid`;
- `resource_release_uid`;
- `deployment_uid`;
- `cluster_uid`;
- `release_kind`;
- `harness`;
- `agent_type`;
- a branch UID;
- a special SDK or agent-runtime image.

Provider, model, and thinking are required Agent runtime defaults rather than
identity. Validate the combination through Django. Use `llm_thinking: off`
when the selected model has no configurable reasoning level.

A CodeRepositoryBranch owns at most one Harness Agent across all workflow
files. Two declarations are invalid even when their keys, resources, or files
differ. The backend enforces this during per-file validation, cross-file event
validation, branch locking, and the database one-to-one relationship.

Automatic deployment builds/selects the ordinary code repository image for the exact
event commit, creates an immutable ResourceReleaseRevision, and deploys it
through the standard DeploymentRun and Knative lifecycle. It never bootstraps
or overlays a separate coding-agent image.

## Jobs

A Job spec declares its name, description, execution path, schedule, command
arguments, compute, timeout, and automatic-redeployment intent. It never
identifies or adopts an existing Job by UID. Django creates or reuses the Job
through the durable branch, workflow-path, and resource-key declaration
binding, then resolves the exact code repository image after policy eligibility.

`scheduled_command_args` is an argument vector, not a shell string:

```yaml
scheduled_command_args:
  - python
  - -m
  - project.jobs.refresh
```

`task_schedule` uses the backend-advertised crontab fields and an IANA timezone.
Validate it through Django rather than reproducing cron rules locally.

## FastAPI And Static Sites

Use the explicit `fastapi` and `static_site` product kinds. Do not use a
generic `resource_release` wrapper or put `release_kind` in the spec.

Complete FastAPI workflow example for an OAuth callback and webhook:

```yaml
api_version: "3.0.0"
name: provider-api
resources:
  - key: provider-api
    kind: fastapi
    spec:
      source_path: api/provider/main.py
      automatic_deployment: true
      automatic_redeployment:
        enabled: true
        tag_regex: null
      revision_retention_count: 3
      public_ingress:
        - method: GET
          path: /integrations/provider/oauth/callback/
        - method: POST
          path: /integrations/provider/webhook/
```

A single FastAPI application factory may expose both HTTP and WebSocket routes.
Do not create a second release or add a `websocket_enabled` field. Platform
transport, gateway, ticket, and timeout policy remains backend-owned.

### Automatic public callback deployment

The repository author or coding agent sets `spec.public_ingress` in the YAML;
the platform never infers public routes from FastAPI decorators or OpenAPI.
Do not set `FASTAPI_PUBLIC_INGRESS` in `env_vars`. Declare the release in this
workflow; Django derives the runtime setting from the immutable revision when
it deploys the app.

1. Implement the handler at the literal method/path declared above in the
   FastAPI app loaded by `source_path`. A `GET` OAuth callback may receive a
   query string; declare only `/integrations/provider/oauth/callback/`, never
   a query or URL. The app verifies OAuth state and exchanges the code, or
   verifies a webhook signature and replay identifier. Keep secrets out of
   YAML and logs.
2. GET the exact branch's workflow template from the route in **Procedure**.
   Use its advertised `api_version`, edit a direct
   `.mainsequence/workflows/*.yaml` file, and put each public pair under the
   `kind: fastapi` resource's `spec.public_ingress`. `[]` means no public
   routes. `automatic_deployment: true` enables deployment; the nested
   `automatic_redeployment` policy governs later repository events. With
   `enabled: true` and `tag_regex` omitted or null, every pushed commit is
   eligible; a regex admits only matching tags created by the repository.
3. POST that exact file's `path` and full YAML `content` to the branch
   `validate-workflow` route in **Procedure**. Continue only when `valid` is
   true. The route validates but does not deploy.
4. Commit both the handler and workflow file, then push the intended tracked
   branch. The repository event applies the declaration, resolves its indexed
   source and exact-commit image, creates or updates the release, and records
   a DeploymentRun. A local file edit or validation response does not deploy.
5. Follow the workflow event result and its DeploymentRun until the candidate
   revision is ready and active. Call MCP `resource_release.get` using the
   backend-returned release UID. Confirm the exact pair appears in both
   `public_ingress` and `effective_public_ingress`, and use the returned
   `public_url`; do not construct a hostname or register the provider URL
   before activation. A failed candidate leaves a new pair private.
6. Append the literal path to `public_url`, register that full URI with the
   provider, and verify a provider-style request reaches the handler without
   a Main Sequence Bearer token. Verify an unlisted path and wrong method are
   denied. Admission supplies no User identity or provider authentication.

On removal, commit and push the updated workflow. Once reconciliation writes
the desired policy without that pair, admission stops immediately, even if
an older revision remains active. Recheck `effective_public_ingress` before
using a registered provider URI.

Static Site example:

```yaml
- key: command-center
  kind: static_site
  spec:
    name: Command Center
    root_directory: web
    framework: vite
    output_directory: dist
    routing_mode: spa
    automatic_deployment: true
    automatic_redeployment:
      enabled: true
      tag_regex: null
    revision_retention_count: 3
```

Static sites do not accept `env_vars` or a runtime image. Use the declared
`build_environment` contract. A value is a string, or names a FastAPI release the
site calls instead of pinning its UID:

```yaml
build_environment:
  VITE_ORDERS_RESOURCE_RELEASE_UID:
    resource_release:
      repository: Orders
      key: orders-api
```

Django resolves it at every build to the one workflow FastAPI release with that
code repository name and resource key in the site's Environment; none or several
fail the build with `invalid_configuration`. When a workflow re-creates that
release (a key or file rename), Django rebuilds the automatically deployed sites
of other branches that name it, at their live commit when that is still their
branch head. Prefer the name over a pinned UID whenever the site is declared in a
workflow. Navigation placement and repository-backed icon
mask fields are accepted only for the workflow versions advertised by the
fresh backend template.
The retired `agent`, `streamlit_dashboard`, and
`code_repository_coding_agent` discriminators are invalid and must not be
translated.

## Automatic Redeployment

`automatic_deployment` is the target's master switch.
`automatic_redeployment` controls later repository-event eligibility:

```yaml
automatic_redeployment:
  enabled: true
  tag_regex: "^v[0-9]+\\.[0-9]+\\.[0-9]+$"
```

The workflow file is the only writer of these settings: the DRF and MCP update
operations reject `automatic_deployment` and `automatic_redeployment_policy`.
Each application writes what the file declares. Omit `tag_regex` (or use
`null`) to deploy every pushed commit. A regex deploys the commit a matching tag
points at, when that commit is the branch head or reachable from it, even after
the branch has moved on; every-push targets ignore such a tag. Matching uses
exact short tag names and full-match semantics. To stop deployments, commit
`enabled: false`.

The platform renders no tag names and chooses no versions. The repository
creates its own release tags, for example with a GitHub Actions job that tags
the merge commit after its tests pass:

```yaml
# .github/workflows/release.yml (repository code, not a platform template)
name: release
on:
  push:
    branches: [main]
concurrency:
  group: release-main
  cancel-in-progress: false
permissions:
  contents: write
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --locked
      - run: uv run pytest
      - name: Tag the version declared in pyproject.toml
        run: |
          TAG="v$(uv version --short)"
          if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
            echo "$TAG already exists; nothing to release"
            exit 0
          fi
          git tag "$TAG" "$GITHUB_SHA"
          git push origin "refs/tags/$TAG"
```

A Vite repository reads its version with
`node -p "require('./package.json').version"`.

For every Job, FastAPI application, or Harness Agent workflow, Django owns the
indexed resource and code-repository-image identities in automatic and non-automatic
paths. Disabling automatic deployment does not expose a UID-based image
selection escape hatch. Static Sites retain their backend-owned build path.

### Development Deploys Every Push, Production Deploys Tags

Use two workflow files scoped to Environments. On the production branch only
the first applies; on the development branch only the second:

```yaml
# .mainsequence/workflows/api-production.yaml
api_version: "3.0.0"
name: api-production
scope:
  environments: [production]
resources:
  - key: api
    kind: fastapi
    spec:
      source_path: api/main.py
      automatic_deployment: true
      automatic_redeployment:
        enabled: true
        tag_regex: "^v[0-9]+\\.[0-9]+\\.[0-9]+$"
```

```yaml
# .mainsequence/workflows/api-development.yaml
api_version: "3.0.0"
name: api-development
scope:
  environments: [development]  # the development Environment's exact name
resources:
  - key: api
    kind: fastapi
    spec:
      source_path: api/main.py
      automatic_deployment: true
      automatic_redeployment:
        enabled: true
```

A push to the development branch deploys at once. A merge to `main` deploys
nothing until a matching tag points at a commit on `main`; with the release job
above, CI creates that tag after the tests pass. Take the exact fields from the
branch's workflow template and validate each file before committing it.

### Creating The Tag

The platform reacts to the tag push, however the tag was made. The short tag
name must fully match `tag_regex`, and the tagged commit must be on the
target's branch (its head or an older commit).

- CI, as in the release job above. This is the recommended path.
- By hand, when the user asks for it:

  ```bash
  git tag v1.3.0 <commit>
  git push origin v1.3.0
  ```

  Lightweight and annotated tags both work. Push tags one at a time: GitHub
  sends no event when more than three tags are pushed at once, so
  `git push --tags` can deploy nothing.
- A GitHub Release: publishing a release with a new tag, in the web UI or with
  `gh release create v1.3.0 --target main`, creates the tag, and that tag
  deploys like a pushed one.

A tag created with the Actions `GITHUB_TOKEN` starts no other GitHub Actions
workflow, but it still reaches Main Sequence. Deleting a tag never deploys or
removes anything. A tag that does not match, or whose commit is only on another
branch, deploys nothing for that target.

### Going Back To An Older Version

Two ways work. Use either only when the user asks for it.

- Revert and release a new version: `git revert`, raise the patch version,
  push, and let CI tag the result. This works for every target, including
  every-push targets, and keeps the version moving forward.
- For a target with a `tag_regex`, push the older version's tag again. GitHub
  sends an event only when a tag is created, so delete the tag on GitHub and
  push it again; it still points at the same commit:

  ```bash
  git fetch --tags origin
  git push origin :refs/tags/v1.2.0
  git push origin v1.2.0
  ```

  The target redeploys that commit exactly as it was released: its code, its
  image and its workflow file, whose settings replace the current ones. The
  commit must still be on the branch and the tag must match the target's
  current `tag_regex`. Every-push targets ignore the tag. Re-pushing a tag
  also re-runs any GitHub Actions workflow that the tag triggers, and a tag
  ruleset may forbid deleting it; use the revert route then. The next release
  tag deploys a newer version again.

## Runtime Environment And Secrets

Workflow YAML can contain only non-secret target configuration. Never place API
keys, tokens, passwords, private keys, runtime credentials, registry grants, or
secret values in it. Use the platform-owned secret and credential flows. A
workload's access to an existing Secret is declared by the Secret's name in
`access.secrets`; the value never appears in the file.

## Workload Identity

Every workload runs as its own workload User: each `fastapi` release, each
`harness_agent` with its runtime release, and each standalone `job`. A
release's backing Job runs as its release's User; static sites have none.

- The platform creates it with the workload, before the first deploy or run,
  so access can be granted early. It is never the person who pushed or
  deployed, never an admin, and holds no seat. It acts with a person's
  authority only when it acts for its requester (below).
- It starts with no access. It reaches only what it created, what its
  declaration grants and what a person grants it by hand.
- Its managers are the people who hold the workload's `manage_workload`: a
  Job's or release's `editor` or `owner`, directly or passed down from its
  branch, an Agent's `editor` or `owner` for its release, and Organization
  admins. They give it policies whose actions they hold and hold the creator's
  rights on what it creates.
- An Agent's backing release follows the Agent's policies; a release's backing
  Job follows the release. An `editor` can change code that uses the
  workload's access. Repository, branch or Project access reaches the Jobs and
  releases it declares, subject to those backing-owner rules. Grant broad edit
  access only when that trust is intended. A workload with edit on another
  workload is still not its manager and cannot grant it access.
- Deleting or renaming the workload in the workflow file ends that User; a
  recreated workload is a new User with no grants. Declared grants come back
  on the next push; grants made by hand must be made again.
- Its access is limited to its current Environment.
- Other workloads read the values of the Secrets they created or were
  granted, and update those granted for edit.
- Harness Agents use ordinary effective-user permissions for Secret operations.
  Keep Secret values and runtime credentials out of logs and model context.

Workload usage is charged to the Organization and consumes no human seat.
Managers can read/change accounting attribution through
`/api/v1/workload-users/<workload_user_uid>/` with
`billing_attribution_user_uid` or `billing_attribution_team_uid`; both null
means Organization attribution. This changes accounting, never permissions or
a person's credit budget. No MCP tool administers this setting.

A `fastapi` or `harness_agent` workload can act for its requester when an
Organization admin enables `acts_for_requester: true`. Supported operations use
the person's ordinary read and write rights, including admin roles, in the runtime Environment,
while the request is being served and for at most 24 hours after it was made.
Every call rechecks access. There is no Agent-specific exclusion or approval list.
Absent delegation uses workload permissions; invalid delegation is rejected. Only an admin's push enables the declaration;
removing a declaration turns off the setting it owned. Give a workload no grants
of its own when it should act only for people.

Declare the setting at resource level, beside `spec` and `access`, never inside
`spec.env_vars`:

```yaml
# Add to an existing fastapi or harness_agent resource beside spec and access.
acts_for_requester: true
```

After creation, an Organization admin may instead PATCH
`/api/v1/workload-users/<workload_user_uid>/` with
`{"acts_for_requester": true}`. A manager may disable it. An API change releases
workflow ownership of the setting; removing an omitted or unrelated declaration
does not undo that manual choice. There is no dedicated MCP setting tool.
An unauthorized enabling push fails with `declared_access_not_granted` before
deployment; use an authorized admin push or remove the declaration.

For Harness work, the original request time survives A2A delegation and caller
resumption. Ending/stopping work, lease expiry, disabling the setting or loss
of requester access can end it sooner than 24 hours. A FastAPI inbound binding
is limited by its signed assertion, at most five minutes. Do not send person
UIDs as proof or retry rejected delegation with workload grants. The trusted
SDK owns proof handling; `a2a-communication` explains exact send inheritance.
Tasks of an enabled Agent are visible only to their creator/recorded requesters
and Organization admins, subject to normal Agent and Environment access.

Clients tell people: "This Agent works with your identity. It can read, create, change, run, share or delete only what your permissions allow through supported operations, only while serving your request, and for at most 24 hours after you ask. It uses your ordinary permissions, including administrative permissions, and access is checked on every call. Your Organization's administrator approved it to work this way."

Prompt injection can steer the Agent into unintended reads, writes, sharing or
deletion within the person's permissions. Completed changes may persist after
access expires. Only Organization admins decide which Agents receive this trust.

There are two ways to give a workload access of its own: declare it in the
workflow file (below; preferred, because the file owns the grant), or grant it
by hand.

## Declared Access

A `job`, `fastapi` or `harness_agent` resource declares the access its
workload requires. Each entry names the policy the workload gets:

```yaml
access:
  branch: reader              # this branch and what follows it
  branches:
    - repository: Pricing     # that repository's branch in this Environment
      policy: reader
  secrets:
    - name: METATABLES_RUNTIME_DB_URI
      policy: value_reader
  constants:
    - name: REGION
      policy: reader
  buckets:
    - name: metatables-exports
      policy: editor
  agents:
    - name: Analyst
      policy: user
  model_providers:
    - provider: openai-codex  # a built-in provider's configured credential
      custom_id: team-openai
      policy: user
    - provider: acme-gateway  # a custom endpoint, by its identifier alone
      policy: user
  resources:
    - key: migrate-system     # another resource of the same file
      policy: runner
```

- `policy` names a policy of the object's type that a workload can hold;
  `branch` takes the policy's name. Choose the smallest one the workload
  needs, and read each policy's texts with `access.catalog` first:
  - a branch (`branch`, `branches`): `reader`, `editor`, `owner`;
  - a Secret: `reader` (not the value), `value_reader` (the current value),
    `editor`, `owner`;
  - a Constant: `reader`, `editor`, `owner`;
  - a Bucket: `reader`, `contributor` (browse and upload), `editor` (also
    rename, move folders and delete), `owner`;
  - an Agent (`agents`, or a `harness_agent` resource under `resources`):
    `user` (use it), `viewer` (also its logs and Tasks), `operator` (also
    cancel its work), `editor`, `owner`;
  - a release (a `fastapi` or `static_site` resource under `resources`):
    `user` (call it), `viewer` (also its logs), `editor`, `owner`;
  - a Job (a `job` resource under `resources`): `viewer` (it and its runs),
    `runner` (also run and stop it), `editor`, `owner`;
  - an Artifact: `viewer` (read and download it), `editor` (also overwrite
    and delete it), `owner`;
  - a model-provider credential or custom endpoint: `user` (use it),
    `editor` (also change or revoke it), `owner`.
  `owner` also decides who has access to the object.
- Validation refuses `level` ("level is replaced by policy in api_version
  3.0.0."), an entry without `policy`, and a policy the type does not have;
  the error lists the type's policies. A `2.3.0` level becomes: `view` gives
  `reader` on a branch or a Constant, `value_reader` on a Secret,
  `contributor` on a Bucket, `user` on a model provider and `viewer` on an
  Agent, a release, a Job or an Artifact; `edit` gives `editor`.
- Workflow files stay UID-free: every object is named in the branch's
  Environment. Secrets, Constants, Buckets and Agents by name, Artifacts by
  Bucket and name, configured model providers as code selects them, other
  resources of the file by key. Secret, Constant and Bucket names are unique
  per Environment; an Agent or Artifact name that matches more than one object
  fails with `ambiguous_name`.
- `model_providers` shares a configured model provider with the workload's
  User, for the model calls it makes as itself: a built-in provider's
  credential by `provider` and the required `custom_id`, a custom endpoint by
  `provider` alone. A credential resolves to the matching one the workload
  already holds, otherwise the pusher's own, otherwise among those the pusher
  can see; one the pusher cannot see is `not_found`, and two of other owners
  are `ambiguous_name`. Before helping write
  this entry, explain that it shares the credentials with the workload's
  runtime: once declared, every later push to the branch keeps the share and
  can change the code that receives them, and usage is billed to the
  credential's owner (the model-provider-credentials skill has the full
  disclosure).
- `branch` is always the declaring branch. A workload that uses another
  repository's Jobs or releases, such as an application that calls the
  MetaTables API (`repository: MetaTables`), declares that repository's branch
  in the same Environment under `branches`, by the repository's exact name,
  case included. The binding reaches the Jobs and releases declared on that
  branch. A repository with no branch in this Environment fails with
  `not_found`, a name that matches two branches with `ambiguous_name`, and the
  declaring repository with `own_branch`.
- The push's resolved person grants an entry when they manage the workload
  (`manage_workload` on it) and hold every action of the entry's policy on
  the object.
  Importing a repository, or setting it up, carries the authority of the
  person who started it the same way. A push or sync with no resolved person
  grants only the branch, what is declared on it, and objects its workloads
  created; never another repository's branch, and never `owner`.
- The declaration gates the resource. It is checked before anything of the
  resource is applied, and its bindings are written before the deployment
  starts. When an entry cannot be granted, the resource fails with
  `reason: declared_access_not_granted` and `access.not_granted` entries with
  the entry's `target`, `policy` and a `reason`; nothing of it is created,
  changed or deployed. Fix the declaration, or get the access from a person
  who holds it, and push again.
- A named resource of the same file is applied first; resources that name each
  other are applied and rolled back together. An Agent named both under
  `agents` and by its Harness Agent resource's key gets one binding, with the
  policy that includes the other entry's.
- The declaration owns the bindings it applied: later pushes re-apply them,
  removing an entry removes its binding, changing it to a policy the binding
  includes needs no authority while any other change needs it again, and a
  binding made by hand that includes the declared policy is never changed.
- Access a workload uses without declaring it fails when the workload reaches
  the object; a person who manages the workload grants it.
- To give access by hand, read `workload_user_uid`, the public UID of the User
  the workload runs as, from its Job, release or Agent (`job.get`,
  `resource_release.get`, `agent.get`), and attach a policy with
  `access.attach`: the object's `resource_type` (`constants`, `secrets`,
  `buckets`, `artifacts`, `jobs`, `resource-releases`, `agents`,
  `model-provider-credentials`, `custom-model-providers`,
  `code-repositories`, `code-repository-branches` or `organization-projects`),
  its `object_uid`, the `policy` (any of the type's policies listed above, or
  `reader` or `editor` on a repository or a Project), `principal_type` `user`
  and the `workload_user_uid` as `principal_uid`. An Agent's release takes its
  policies on the Agent. Prefer the declaration, which keeps the binding across pushes. The
  manager gives policies whose actions they hold on the object; the object's
  owner may give it too when they can view the workload. Directories and candidate lists offer workload Users only
  when asked with `identity_type=workload`, and only those the caller can view.
  `workload_user_uid` is `null` for a release's backing Job,
  which runs as its release's workload User, for a static site, and for an
  Agent without a runtime release. An Agent and its runtime release return the
  same value.

Harness Agent runtime setup and local development are not owned by the
deployment workflow. Use the `code-repository-to-agent` skill for the handoff
to the installed runtime library's version-matched instructions.

## Observation And Failure Handling

After application, inspect the workflow result and referenced DeploymentRun.
Pending image dependencies, queued/running provider work, candidate revisions,
readiness, and activation are separate durable states. Do not repeat create
calls after an ambiguous provider response; reconcile the existing run.

A failed desired revision does not displace a prior ready active revision.
Retained revisions keep their exact image dependencies until canonical
retention cleanup removes them.

When validation fails, report exact backend paths and error codes. Do not invent
a corrected branch, internal UID, image, Agent UID, or deployment kind.

## Workflow Environment applicability

Optional root `scope.environments` is a non-empty list of unique exact
Environment names. Omission applies in the event branch's existing Environment.
`production` matches its backend-owned production role; other names match
case-sensitively. This is a file applicability filter, never Environment
selection. A nonmatching file is structurally validated but skips source/target
resolution, reconciliation and automatic deployment. It declares nothing on
that branch, so the branch's targets bound to it are deleted. Validation reports `applicable` for scoped
files. Generic deployment handlers honor the same exact-event result.


## Deployment Job dependencies

The optional `execution.steps` mapping coordinates general Jobs around a
release. A step contains one `prepare_image`, `run_job`, or `deploy` resource
reference. `needs` references local step names; independent ready Jobs can run
in parallel. Runtime deploy and Job steps require `image_from` pointing to a
prepare-image ancestor. Static Site deploy owns its build and has no image_from.
Job `command_args` defaults to an empty list and never inherits scheduled args.

Django creates or reuses the declared Jobs; do not ask the user to create them
first or paste UIDs into YAML. A Job declaration without run_job keeps its
manual/scheduled behavior. The backend freezes the exact image and invocation
inputs, and waits for settled JobRun success before starting dependent work.
Every represented target's promotion policy must admit its connected group.
Optional scope.environments remains independent of execution and never remaps
the Environment. Neither field is required for existing workflows.

A migration is an ordinary Job, as are initialization, validation and smoke
checks. A failed prerequisite blocks descendants. A failed post-deployment Job
fails the workflow while the successful deployment remains active. There is no
automatic script replay, rollback, workflow retry endpoint, expression language,
output passing or cross-file dependency support. Duplicate events and recovery
reuse the same invocation. Jobs touching shared external data own their locking
and idempotency.

Inspect the latest 20 runs using the existing branch read with
`include=workflow_runs`, or MCP `code_repository_branch.get` with
`include: "workflow_runs"`. Named steps reference canonical JobRun and deployment
UIDs; follow their existing status/log reads. Steps carry their sealed `needs`
and `image_from` and their dispatch times, so draw the graph from the read, never
from the YAML. From a deployment run, `deployment_run.get` or
`deployment_run.list` with `include: "workflow_run"` returns the workflow run it
serves and `workflow_step_keys`. The two workflow models coordinate
those owners and do not replace their execution lifecycle.


## FastAPI MCP capability

A FastAPI API may expose an application-owned authenticated `/mcp` alongside its
HTTP routes. Workflow API `3.0.0` accepts `spec.mcp_enabled` (boolean, default
false). The flag is desired release configuration, fingerprinted and snapshotted
in immutable revisions; only the ready active revision advertises a connection.
Workflow omission resets desired state; PATCH omission preserves it.
`resource_release.mcp_endpoints` lists the FastAPI MCP endpoints a person can
use; `resource_release.list` accepts `mcp_available=true` and returns nullable
`mcp_connection` metadata, also present on detail. A cold runtime may have an
active connection; this is not a live-health guarantee.

Call this project endpoint **FastAPI MCP** and the central platform endpoint
**Django MCP**. Direct clients need only the FastAPI MCP URL: the release origin
serves the login metadata, and clients sign in through shared Django OAuth,
without Command Center or a preconfigured Django MCP. Existing access and
refresh JWT types are bound to that project resource; they do not authorize
central platform operations. Discovery does not install client configuration.

The application owns tools, business authorization and FastAPI/MCP lifespan;
shared REST/MCP/harness functions are an optional application design choice. No registry,
generated REST adapter, Tau adapter or separate runtime is required. The SDK's
MCP hosting/context integration is owned by its existing ADR 0038, with caller
identity owned by ADR 0036; both are linked from platform ADR-0050. Verify
launcher compatibility and client startup behavior before enabling a project;
backend support alone does not prove that the deployed application has the
mount.

Use the `resource-release` skill's FastAPI MCP sections to find endpoints and
connect clients, including browser consent, supported stateless POST transport,
Origin checks and safe startup retry. The flag cannot be combined with anonymous `/mcp` public ingress.
Installing a mount and composing its startup/shutdown belongs to the application
and its version-matched SDK. Do not set protected `FASTAPI_MCP_ENABLED` in
`env_vars`; the platform projects it from the immutable revision. Verify the
installed Tau registration contract before promising `mcp_applications` tools.
