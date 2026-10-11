---
name: resource-release
description: Declare ResourceReleases in repository workflows, then configure, deploy, inspect, and delete existing releases through canonical MCP operations.
---

# Main Sequence Resource Release

Use this skill for the shared ResourceRelease lifecycle. Main Sequence MCP
projects the canonical DRF operations; it does not implement another release,
deployment, permission, or retry system.

Read `mainsequence://platform/ontology` before operating on CodeRepositoryBranch,
ResourceRelease, CodeRepositoryJobImage, PublicCatalogImage, or DeploymentRun
identities. Read the separate
`mainsequence://platform/skills/static-site` skill when the target release kind
is `static_site`.

Create ResourceReleases by declaring them under `.mainsequence/workflows/`;
read the `code-repository-workflows` skill for the exact file contract. Public
collection POST and the former `resource_release.create` MCP tool are retired.

## Preserve Canonical Ownership

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

Pod Manager owns release persistence, validation, authorization, image and
source resolution, automatic promotion, deployment orchestration, and run
state. MCP owns only protocol adaptation and this operation guidance.

Every release owns immutable `ResourceReleaseRevision` identities plus
`active_revision`, `desired_revision`, and `revision_retention_count`.
Active/desired values returned by release list/detail are nullable revision
UIDs, not provider names or browser URLs. Only a ready active revision serves
the existing stable release URL; a failed desired revision leaves prior traffic
unchanged. DeploymentRuns remain separate attempts.

Set `revision_retention_count` through a workflow declaration or existing release update
operation when a different rollback history is required. The value is a
positive integer, defaults to `3`, and belongs to the release, not
`automatic_redeployment_policy`. Active, desired, and live-run references
remain protected. Revision candidate discovery is
asynchronous: successful runtime activation and a retention edit enqueue
backend reconciliation after commit, and a periodic database sweep recovers
lost Celery wake-ups. Editing the count does not synchronously delete provider
artifacts. Static-site cleanup remains target-specific.

Every retained runtime revision pins its exact `CodeRepositoryJobImage`, not
only the active or desired revision. Before deleting an apparently unused
image, inspect its release-revision provenance; remove the owning revision only
through the canonical retention lifecycle, never by bypassing image protection.
Runtime retention then submits the former image to canonical guarded deletion;
the image and registry artifact are deleted only when no other canonical
dependency uses them.

For image-backed deployment, the parent `DeploymentRun` owns role-specific
`DeploymentRunImageDependency` relations and `CodeRepositoryImageBuildRun` owns the
complete provider attempt. Provider request/operation state and image
UID/URI/digest/readiness are never owned by generic run JSON or duplicated on
`CodeRepositoryJobImage`. Preparation commits before provider submission; database
due-action state recovers lost Celery wake-ups. An ambiguous submission is
reconciled on the same attempt during a bounded resolution window and must not
be retried through another create call. If no provider handle or output can be
adopted by the deadline, that attempt and its awaiting dependencies fail; a
delayed observation cannot rewind it. Runtime admission requires the verified
dependency's digest-pinned output and never selects `latest`.

The persisted image model is ownership-typed. Public catalog inputs use
`PublicCatalogImage`; deployable code-repository outputs use the Organization-owned
`CodeRepositoryJobImage` descendant. There is no generic persisted `Image`, union
lookup, or ownership inference. Workflow creation resolves the exact code-repository image internally; backend catalog selection never asks the caller for a public-image UID.

Repository workflows can declare `fastapi`, `static_site`, and
`harness_agent` releases. For an ordinary FastAPI OAuth callback or webhook,
set `public_ingress` in the workflow `kind: fastapi` spec or update an existing
release with `resource_release.update`. A newly added route becomes effective
only after a ready revision snapshots and deploys it. `resource_release.get`
supplies the backend-issued `public_url`, desired `public_ingress`, and
`effective_public_ingress`; use these values to register the provider URL
after activation. The app verifies OAuth state or webhook signatures; public
admission supplies no Main Sequence User identity. Django sets the reserved
runtime `FASTAPI_PUBLIC_INGRESS` value from the active revision.

Main Sequence-managed Streamlit dashboard deployment and the Google ADK
`agent` ResourceRelease are retired. Stop if a request or repository workflow
declares `streamlit_dashboard`, `agent`, or `code_repository_coding_agent`; do
not translate an existing release, infer a replacement, or use a legacy
launcher.

`harness_agent` is a third persisted release kind, but it is workflow-only:
the repository declares `kind: harness_agent`. The workflow backend creates the Agent
identity, persists `release_kind=harness_agent`, and binds the two. The caller
never supplies `agent_uid`, `resource_uid`, `related_image_uid`, `release_kind`,
or another platform UID for that workflow intent. Agents owns the semantic
Agent, card, and provider/model/thinking defaults. Django Pod Manager owns the
bound release, exact source/image resolution, DeploymentRuns, revisions, and
the Harness Agent deployment adapter.

Every ResourceRelease belongs to one exact CodeRepositoryBranch. Use the public
CodeRepositoryBranch UID for branch-scoped discovery and never substitute a logical
CodeRepository UID.

`ResourceRelease.uid` is also the sole public runtime target. Release
responses do not expose a separate `subdomain`, and clients must not derive or
send numeric, product-specific, internal-service, or tenancy-qualified target
aliases. When a canonical operation returns a public or RPC URL, consume that
URL as opaque connection data instead of constructing a product hostname.

## Discover Existing Releases

Use `resource_release.list` with bounded `limit` and `offset`. Prefer exact
filters such as `name`, `code_repository_branch_uid`, `release_kind`, or `uid` before free-text
search. The response is the canonical paginated collection with `count`,
`next`, `previous`, `results`, `controls`, and `actions`.

`name` matches the exact shared release name for both runtime and static-site
releases. Names can repeat across branches; use the deployment's owning branch
UID or release kind to disambiguate and retain the returned release UID. A
shared MetaTables API can belong to a different CodeRepository than its consumer,
so do not assume that the consuming repository's branch owns the deployment.
These filters narrow the existing authorized collection and grant no access.

Use `resource_release.get` with `resource_release_uid` before configuration or
deployment. Detail is discriminated by `release_kind`; do not assume runtime
fields exist on a static site, or that static configuration
exists on a runtime release.
Collection `actions` describe authenticated DRF collection actions for user
interfaces. They do not create unregistered MCP tools. Never invoke an action's
raw endpoint through a generic HTTP or API proxy.

## Prepare Runtime Release Intent

Declare the FastAPI source path, stable resource key, runtime configuration,
and automatic redeployment policy in `.mainsequence/workflows/`. Django
resolves the indexed resource and exact event image. Do not supply numeric or
public source/image UIDs in the declaration. Inspect the resulting
DeploymentRun and release UID before operating on the existing release.

## Deploy One FastAPI App With REST And WebSockets

A single FastAPI application factory may return one `FastAPI` instance that
contains both ordinary HTTP/REST routes and FastAPI/Starlette WebSocket routes:

```python
from fastapi import FastAPI, WebSocket


app = FastAPI()


@app.get("/orders/{order_id}")
async def get_order(order_id: str) -> dict[str, str]:
    return {"order_id": order_id}


@app.websocket("/ws/orders")
async def orders(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"status": "connected"})
    await websocket.close()
```

Deploy that application as one existing `fastapi` ResourceRelease. Both route
types use the same `resource_uid`, image, immutable release revision, stable
release URL, Knative Service, and Uvicorn process. Do not create a second
ResourceRelease, release kind, hostname, namespace, Job, image, or workflow
resource only for WebSockets. Bare Starlette application factories are not
supported by this release contract.

Use one explicit `kind: fastapi` declaration from the
`code-repository-workflows` skill. Its repository-native `source_path` loads
the combined FastAPI instance while Django resolves the internal indexed
resource and image. Effective automatic deployment derives one exact event
image, creates the existing `resource_release.fastapi.build_and_deploy` run,
and promotes both transports atomically behind the stable release URL. Later
eligible commits redeploy the whole application. There is no
`websocket_enabled` workflow field or WebSocket-specific DeploymentRun.

WebSocket runtime compatibility is platform-owned and is part of every standard
FastAPI runtime. Django projects the reserved connection policy after project
`env_vars`; Pod Deployment Orchestrator validates the approved runtime and
always starts Uvicorn with the supported `websockets-sansio` adapter;
infrastructure controls public upgrade forwarding on the shared FastAPI
gateway. Configuring the adapter does not create a latent WebSocket connection;
per-connection state exists only after an incoming upgrade attempt. Do not put
enablement, protocol-adapter, ping, message-size, compression, ticket-store, or reserved
authentication-subprotocol settings in workflow `env_vars`. The platform
compatibility guarantee applies to the approved base runtime; dependency
overrides in CodeRepository images remain user-owned and can fail candidate
readiness without replacing the current active revision.

The standard launcher automatically provides the exact
`GET /ms-health-deployment` application-readiness endpoint for ordinary
FastAPI and Harness Agent releases. Define project business routes normally;
the launcher injects this reserved endpoint outside project middleware, and
`ms-tau-sdk` supplies the Harness Agent readiness predicate. Backend capability
evidence decides whether Knative uses that HTTP path or the temporary TCP
compatibility probe. Keep ResourceRelease workflows and Blueprints focused on
the application contract because the platform owns this runtime infrastructure.

For a platform-deployed FastAPI release, obtain usable runtime access through
the Main Sequence client method that resolves and waits for ResourceRelease
runtime access. Django creates or reuses the exact-revision activation and
returns `ready`, `waking`, or `unavailable`; while waking, wait using its
bounded `retry_after_ms` hint. Send the business request only after
`can_request=true` and use the credential returned in that same ready response.
Local/debug processes use their launcher-owned readiness flow because Django
does not own those processes. MCP release lifecycle tools continue to manage
deployment configuration and do not duplicate the runtime-access operation.

For an application operation that needs to act for the inbound FastAPI caller,
the `X-User-UID` gateway header alone is not proof. The staged ADR-0046
contract requires a verified, release- and Environment-bound signed caller
assertion alongside the revision-bound workload credential on an actor-aware
Django operation. Do not enable that application behavior until gateway
forwarding and runtime verification are deployed. Service-only calls use the
workload identity without a caller assertion.

REST requests continue to use the existing FastAPI Bearer flow. A non-browser
WebSocket client that can set handshake headers may use that same UID-bound
Bearer credential. A browser obtains a fresh one-time, target/origin/path-bound
ticket through the canonical application route:

```text
POST /api/v1/resource-releases/{resource_release_uid}/websocket-ticket/
body: {"origin": "https://app.example.com", "path": "/ws/orders"}
```

The no-store response supplies `websocket_url`, expiry, and the reserved
`mainsequence.ws-ticket.<ticket>` subprotocol. The browser sends that value
first in `Sec-WebSocket-Protocol`, the fixed non-secret
`mainsequence.ws-bridge.v1` acknowledgement second, and any application
subprotocols after them. The acknowledgement is hard-coded in the browser SDK,
gateway, and runtime; it is not a Django response or storage field. The gateway
removes both platform values before the request reaches the application. On a
successful upgrade, `socket.protocol` is the upstream-selected offered
application protocol, or the bridge acknowledgement when the application
selects none.

Ticket acquisition is application runtime behavior, not a deployment-time MCP
operation; no browser-ticket MCP tool exists, and an MCP agent must not invoke
the unregistered route through a generic proxy. Django mints and validates the
ticket before the upgrade. After `101 Switching Protocols`, frames flow gateway
to Knative to the pod without passing through Django.

After deployment, observe the one returned DeploymentRun to terminal success,
then verify at least one authenticated REST request and one authenticated
WebSocket upgrade/message/close cycle against the stable release URL. A
successful deployment does not by itself prove that the shared gateway has
been enabled for public WebSocket upgrades. Existing connections may drain or
disconnect during revision promotion; clients reconnect to the stable URL and
browsers obtain a new ticket for each connection.

## Prepare Static-Site Inputs

For `static_site`, read the separate static-site skill and call
`resource_release.static_site_capabilities` for the exact CodeRepositoryBranch before
creation or static configuration changes. That live DRF response owns supported
fields, defaults, choices, conditions, and constraints. The installed Command
Center SDK skills own frontend implementation.

## Configure Automatic Redeployment

`automatic_deployment` is the master switch for repository-triggered
redeployment, and the nested target-owned `automatic_redeployment_policy` is
the only promotion rule. Both are set only by the repository workflow file's
`automatic_redeployment` block; releases expose them read-only, and
`resource_release.update` rejects them.

- An omitted or null `tag_regex` deploys every pushed commit while enabled.
- A bounded regex requires a full match against a valid short Git tag pointing
  to the exact commit. That commit may be the branch head or an older commit
  reachable from it: a tag on an older commit deploys that commit (a tag
  promotion), which every-push releases ignore. The repository creates its own
  tags, for example in CI; the platform renders no tag names.
- To change either setting, edit the workflow file and push.

## Create A Release

Add one `fastapi`, `static_site`, or `harness_agent` declaration under
`.mainsequence/workflows/`, then use the CodeRepository workflow validation
and application path. Django owns exact source and image resolution and the
initial deployment. The creation response or release UID is not proof that
the first deployment is active; inspect the DeploymentRun and active revision.

Workflow APIs `2.0.0` through `2.3.0` accept target-owned non-secret
`env_vars` for runtime releases. Omission preserves, an empty list clears,
and a present list replaces the mapping. Static Sites use
`build_environment` instead.

## Configure FastAPI Browser Origins

When FastAPI creation omits `cors_allowed_origins`, the backend persists the
active platform static-site wildcard already selected by its deployment
environment. Development resolves to:

```yaml
cors_allowed_origins:
  - "https://*.site-dev.main-sequence.app"
```

Production resolves to `https://*.site.main-sequence.app`. This platform
deployment environment is not an Organization Environment, and callers must
not derive the value from a CodeRepositoryBranch or
`OrganizationEnvironment`. Retrieve the backend workflow template when
authoring a workflow; do not copy the development value into production.

Pass the canonical field on create, partial update, or an API `2.2.0` or `2.3.0`
code-repository workflow declaration only when an explicit override is intended.
Creation omission uses the platform default. Update or workflow-reconciliation
omission preserves the stored policy. An explicitly submitted `[]` denies all
browser origins, while a non-empty list replaces the policy.

The list accepts at most 32 exact HTTP(S) origins or a wildcard only as the
complete leftmost hostname label. The backend normalizes and deduplicates the
effective list, persists it on that FastAPI release, and supplies the platform-owned,
comma-separated `FASTAPI_CORS_ALLOW_ORIGINS` setting on the next deployment.
Do not put that reserved setting in workflow `env_vars`.

```text
FASTAPI_CORS_ALLOW_ORIGINS=https://*.site-dev.main-sequence.app
```

Starlette's `allow_origins` accepts exact origins and does not interpret that
glob by itself. The launcher validates the glob and compiles it to an anchored
`allow_origin_regex` equivalent to:

```yaml
cors_allowed_origin_regex: '^https://[^.]+\.site-dev\.main-sequence\.app$'
```

The public release contract intentionally does not accept an arbitrary regex.
For understanding or testing the stricter platform UID-host policy, the
preferred equivalent regex is:

```yaml
cors_allowed_origin_regex: '^https://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.site-dev\.main-sequence\.app$'
```

The `*` policy authorizes browser JavaScript from every matching
`<site-uid>.site-dev.main-sequence.app` static-site deployment origin to make
and read CORS-enabled requests to that FastAPI release. It does not match the
parent domain or nested subdomains. CORS is not API authentication: the caller must
still present the FastAPI release's valid Bearer token and target the exact
release UID.

The public gateway must complete unauthenticated `OPTIONS` preflight through
Django's final response, without invoking the release runtime. This requires
the infrastructure #41 gateway cutover after the Django deployment. Preflight
uses the current effective origin policy; application-response CORS headers
still require runtime redeployment. Normal private requests remain
Bearer-authenticated. See the ResourceRelease reference's trusted FastAPI
preflight contract for descriptors, methods, headers, and rollout requirements.

## Compose A Static Site With One FastAPI Release

Before implementing the composition, call `resource_release.get` for the exact
target FastAPI release and inspect its returned `cors_allowed_origins`. Compare
that policy with the source release's backend-returned `public_url`; do not
construct either hostname. An empty or non-matching target policy makes the
delegated exchange return `403` with `code=origin_not_allowed`.

If the accepted policy must change, update the repository workflow, validate
it, and push an eligible exact repository event. Retrieve the target again to
verify the persisted value and observe the DeploymentRun to terminal success
before testing browser preflight or application requests.

The existing authenticated ResourceRelease action supports a narrow delegated
FastAPI exchange without adding another credential endpoint:

```text
GET /api/v1/resource-releases/<fastapi_release_uid>/exchange-launch/
    ?static_site_release_uid=<static_site_release_uid>
```

Use the exact source StaticSiteRelease UID and exact target FastAPI
ResourceRelease UID. A target UID compiled into the site survives API
deployments, but a rename or removal of the API's workflow key or file replaces
the release; the call then returns 404 until the site is rebuilt with the new UID.
A workflow static site that names the release in `build_environment` is rebuilt
with the new UID automatically. The backend derives the source origin; never submit or
infer an origin, user, Organization, branch, environment, target URL, or token
claim. The target FastAPI release must explicitly admit that source through its
current `cors_allowed_origins` policy.

Issuance and every delegated validation require an active user with current
view access to both releases, servable/routable release state, same-Organization
ownership, an exact Origin match, exact target identity, and current CORS
admission. The releases do not need to share a CodeRepositoryBranch or
OrganizationEnvironment. CORS remains browser admission and never
replaces the delegated credential or code-repository route authorization.

The successful no-store response supplies an opaque `rpc_url`, the exact
target `resource_release_uid`, expiration, and a maximum-five-minute token
bound to the source release, source origin, user, target, and FastAPI RPC
purpose. This flow never transfers the user's general platform JWT to code-repository
code. Frontend acquisition and transport belong to the installed Command
Center SDK skill bundle; do not duplicate its message protocol or TypeScript
API here.

## Read The Authenticated FastAPI Request User

Install `mainsequence.server.fastapi.install_request_identity(app)` once in
the application factory, following the installed SDK's version-matched skill.
The SDK verifies the signed caller assertion and manages request context;
Pod Deployment Orchestrator validates that integration at startup and owns
hosting. Do not build another caller resolver from headers.

```python
from fastapi import FastAPI
from mainsequence.client import User
from mainsequence.server.fastapi import install_request_identity


app = FastAPI()
install_request_identity(app)


@app.get("/me")
def get_me() -> dict[str, str]:
    caller = User.get_logged_user()
    effective_user = User.get_requester() or caller
    return {"uid": str(effective_user.uid)}
```

`User.get_logged_user()` is the authenticated caller, a person or workload.
`User.get_requester()` is the verified delegated person when present. Apply
application permissions to the requester when present and otherwise to the
caller, including that User's ordinary admin/team rights. Keep the caller for
audit. A missing delegation is ordinary caller access; invalid proof rejects
the request and never falls back. Identity alone grants no business permission.
Compatibility `request.state.user` and `request.state.user_uid` describe that
same caller; there is no numeric `request.state.user_id`.

The gateway removes client-supplied identity, forwards Django's signed proof,
and removes the release Bearer before application code runs. A UID header alone
is never proof. Local Bearer validation belongs to the same SDK integration.
Public ingress, preflight, health and browser WebSocket tickets do not acquire
this signed Bearer caller proof; use their own documented contracts. Receiving
a nested requester does not permit forwarding it to Django as a new binding.
Coordinate compatible SDK, launcher and gateway releases before claiming this
works in a deployed application.

## Runtime authentication and recovery

FastAPI and Harness Agent revisions authenticate to Django as their own
workload User using a short-lived projected token. The platform supplies
`MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and
`MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE`; the SDK reads the current file for
each exchange. Do not author these values, copy the token into `.env`, inspect
it through MCP, or configure a bootstrap secret. The secret exchange is removed.
JobRuns retain their per-run session authentication; static sites have no
runtime credential.

Projected-token support starts at `mainsequence` 9.0.8 and `ms-tau-sdk` 1.4.0.
These are the minimum for this authentication mechanism, not proof of support
for later caller/delegation/MCP changes. Check the installed version's skills
and rebuild pinned older dependencies before the next deployment. Existing
credentials bound to an old human principal require a new revision; do not
repair them by copying credentials or reactivating the old principal.

For exchange failures, a missing/unreadable token file or `401` requires fixing
runtime configuration or deployment; never switch to a secret or human token.
`400` for `credential_secret` means an obsolete client. Let the SDK retry
`429`/`503` with bounded backoff and `Retry-After`. The access token lasts
15 minutes; retirement/revocation is checked on later calls. A new deployment
and a non-secret authenticated operation provide evidence of recovery.

## Update Configuration

Call `resource_release.update` with `resource_release_uid` and only fields that
belong to that release kind.

Runtime releases accept only:

- positive release-owned `revision_retention_count`;
- `min_scale`, `max_scale`, `container_concurrency`, `concurrency_target` and
  `timeout_seconds` within the limits described above; and
- for FastAPI only, `cors_allowed_origins`, `public_ingress` and `mcp_enabled`.

Added `public_ingress` paths become effective only after a matching ready
revision deploys and activates. Removed paths lose public admission
immediately; an explicitly submitted `[]` removes all public admission.

Static sites additionally accept the canonical static configuration fields
advertised by `resource_release.static_site_capabilities`, including the
complete write-only `build_environment` map.

An update replaces the submitted configuration values and returns the canonical
release detail. It does not deploy the release. Re-read the release after an
ambiguous result before deciding whether another update is necessary.

Browser build-environment values are not secret storage. Never place a token,
credential, private key, or secret value in `build_environment`. Submitted
values are write-only and responses expose only their keys.

A FastAPI release runs as its workload User (`workload_user_uid`), which has
no access until it is granted. Give it Secrets, Constants, Buckets and other
objects through `access` in its workflow file, or by hand; the
code-repository-workflows skill (Workload Identity, Declared Access) explains
both.

## Deployment creation

Repository workflow application and eligible repository events create new
deployment runs. Public manual deployment creation is retired. Inspect the
repository-event result and the resulting DeploymentRun before assuming a
release is serving the new revision.

A runtime ResourceRelease receives a backend-derived public CodeRepositoryBranch
context during runtime-credential exchange. The deployed SDK uses that
authenticated context for branch-owned operations without requiring a Git
checkout. The container cannot select another CodeRepositoryBranch or Organization
Environment through branch text, environment values, request data, or image
metadata.

The exchanged credential authenticates the release's workload User, independently
of its creator, deployer or billing attribution. Calls without delegation use
that workload's ordinary grants. Verified delegation uses the person's ordinary
permissions; invalid supplied delegation is refused. Runtime proof, billing and
audit retain the workload, and the runtime Environment still bounds access.
Read the workflow skill's Workload Identity section for grants and requester
settings, and the A2A skill for private source/delegation proof handling.

## Observe Deployment State

Use `deployment_run.get` for the returned run UID. Use
`deployment_run.list` for bounded history, normally filtering by
`target_uid=resource_release_uid` and the correct target discriminator:

- runtime releases use `target_type=resource_release`;
- static sites use `target_type=static_site`.

Treat `queued` or `running` as accepted work, not successful deployment. A
release detail and a DeploymentRun describe different state: the release is
the durable target configuration; the run is one deployment attempt.

Identify that attempt from the typed `target` projection. Present
`target.display_name` as one concise human target label. Keep repository,
branch, product type, kind, and revision in their own fields or dedicated UI
columns; never concatenate them into one target string. In global Deployment
History, lead with a two-line Revision cell containing the commit and labeled
revision/release UID, then a separate two-line Branch cell containing repository
and branch. Do not repeat the commit in another column. Place the exact target,
product, resource, and agent identities in separate deployment-detail summary
fields. The revision label prefers the immutable short commit and falls back to
the canonical release-revision or configuration identity. Use the nested
`code_repository`, `code_repository_branch`, `resource_release`,
`code_repository_resource`, and `agent` objects when an exact product identity
is needed. Never present legacy `target.name` by itself: it is a compatibility
snapshot and can contain technical resource keys such as `tau` or `api`. For
Harness Agent runs, `target.uid` is the owning ResourceRelease UID while
`target.agent.uid` is the distinct Agent UID.

Read whole-attempt status from root `state`. Read progress only from
`pipeline.current_step_key` and the complete ordered `pipeline.steps` list,
which is present in both list and detail. Do not infer stages from `target_kind`
or expect a separate phase. `pending` means a declared future step has not
started; waiting for image/provider work leaves the build step `running`; after
a failure, later prevented steps are `skipped` with
`outcome=run_terminated`. Inspect the failed step's `error` before the root
error when explaining where execution stopped.

Read image-lifecycle cost from the run's `billing` object. Its scope is exactly
`image_lifecycle`: it may include an attributed image build, registry storage,
and registry service, but it is not the release's runtime-compute total. A
deployment that reuses an existing image has zero incremental image-build cost;
do not copy the producer build's charge onto the consumer. `pending` with a
null total means asynchronous pricing or persisted allocation is incomplete.
Do not interpret a priced terminal zero as missing work, and do not infer or
request internal provider, rate, allocation-evidence, or diagnostic fields.

Read persisted Knative runtime attribution from `runtime_billing`, whose scope
is exactly `knative_runtime`. Read the combined available amount from
`cost_summary`; `is_complete=false` means an activation remains live or at
least one constituent cost remains unpriced, so the total must not be presented
as final. A priced live amount is the current persisted cost, not a forecast.
Priced amounts are JSON numbers with up to six decimal places, while unresolved
amounts are null. These reads never materialize or refresh billing state.

Use `mainsequence://platform/skills/log-exploration` for authorized
ResourceRelease application logs and DeploymentRun build/orchestration logs.
Do not fetch a logs URL generically or reproduce the log-query contract here.

## Delete Releases And Images

Delete only after the user has explicitly selected the exact public UID and
requested the destructive action. Re-read the target immediately before the
delete when its identity or current use is uncertain.

Call `resource_release.delete` with only `resource_release_uid`. The tool
dispatches the canonical ResourceRelease destroy action and preserves its
operation authorization, scoped lookup, edit checks, dependency protection,
target-specific cleanup, and errors.

- Runtime release deletion removes the release and its generated Job, schedules
  the existing UID-named Knative cleanup, and returns `{}` after the canonical
  HTTP 204 success. It never mutates DNS, TLS, a Front Door custom domain, or
  an edge route; those shared wildcard resources are infrastructure-owned.
- Static-site deletion starts the existing durable asynchronous deletion flow
  and returns the canonical release representation with HTTP 202. Static sites
  share the same infrastructure-owned environment edge, so deletion does not
  mutate DNS, TLS, or a Front Door domain. Treat a `deleting` lifecycle as
  accepted cleanup, not completed deletion.
- A conflict or target-cleanup failure is a failed delete. Preserve the
  canonical error and do not claim that the target was removed.

Call `code_repository_image.delete` with only `code_repository_image_uid` when an exact image is
no longer required. It dispatches the canonical CodeRepositoryJobImage destroy action,
including live-resource protection, typed detachment of terminal build,
deployment, and JobRun history, and registry-artifact cleanup. Active runs or
live Jobs return the canonical structured conflict; terminal records retain
their immutable typed image snapshots without retaining the image row. Success
returns `{}` after the canonical HTTP 204 response.

Delete dependent ResourceReleases before deleting an image they use. Neither
delete tool is a bulk operation or a generic API proxy. Do not automatically
retry an ambiguous destructive response: use `resource_release.get` or
`code_repository_image.get` first to determine whether the object still exists.

## Stop Conditions

Stop and ask for direction when:

- the CodeRepository and exact CodeRepositoryBranch cannot be distinguished;
- a runtime source or image does not belong to the intended CodeRepositoryBranch or
  commit;
- a static field is not advertised by the live capability response;
- a requested update field is not valid for the target release kind;
- a delegated static-site origin is not admitted by the target FastAPI policy
  and the intended exact or wildcard override is not known;
- a build environment value would expose secret material;
- a create, update, or deployment response is ambiguous; or
- the requested bulk action, log read, arbitrary-commit deployment, or other
  operation has no registered MCP tool.


## FastAPI MCP capability

A FastAPI API may expose an application-owned authenticated `/mcp` alongside its
HTTP routes. Workflow API `2.3.0` accepts `spec.mcp_enabled` (boolean, default
false). The flag is desired release configuration, fingerprinted and snapshotted
in immutable revisions; only the ready active revision advertises a connection.
Workflow omission resets desired state; PATCH omission preserves it.
`resource_release.mcp_endpoints` lists the FastAPI MCP endpoints you can see
with their `mcp_connection`. Present each release name with its
`organization_environment_name`; keep `organization_environment_uid` and the
release UID for exact tool calls, using them for display disambiguation only
when names are still ambiguous. `resource_release.list` accepts the same
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
generated REST adapter, Tau adapter or separate runtime is required. Existing
Main Sequence SDK ADR 0038 owns MCP hosting and ADR 0036 owns request identity;
use the installed version's instructions. Backend support alone does not prove
that the deployed application has the mount or compatible SDK/launcher/gateway.

### Connect a client to a project

1. Discover with `resource_release.mcp_endpoints`, using the intended
   branch/name filters, or inspect a known release with `resource_release.get`. Retain `mcp_connection.url`, `transport`,
   `resource_metadata_url`, `organization_environment_uid`, `organization_environment_name` and
   `active_revision_uid`. Use the returned URL exactly; do not build a hostname
   or substitute the desired revision. A known canonical project URL also
   allows direct registration without a Django MCP connection.
2. If `mcp_connection` is null, inspect the desired flag, active revision and
   DeploymentRun. Enabling `mcp_enabled` does not mount a server or immediately
   activate it. Push the implemented application and declaration through the
   normal workflow, then re-read the active connection. A failed desired
   deployment preserves the previous active capability; disabling takes effect
   when the corresponding revision activates. Rollback follows its snapshot.
3. When asked to configure a client, use that client's supported connection
   interface with the returned URL and Streamable HTTP. Discovery itself does
   not install connections. Preserve unrelated client settings; never put
   credentials in configuration text or conversation content.
4. Let the client follow the `401` challenge to Django's resource metadata and
   shared browser login. Signing in alone does not approve a client: the person
   reviews the client, callback host, resource and requested access, then allows
   or denies consent. The client owns OAuth/PKCE and credential storage; no
   pasted token, callback code, Command Center session or central MCP login is
   required. A project OAuth credential is bound to that exact project and
   cannot be used for another project or general platform operations.
5. Validate initialization and read-only tool discovery before claiming the
   connection works. Protected requests can activate a cold runtime; public
   authentication discovery cannot. Follow bounded startup retry guidance for
   safe discovery. After a business tool may have executed, inspect its result
   or application idempotency contract before retrying, especially mutations.

The supported initial profile is stateless, bounded JSON over canonical
`POST /mcp`. Stateful sessions, subscriptions, resumable streams and MCP task
execution are outside that profile; GET/DELETE at the mount and WebSocket
upgrades are not supported. Do not use a trailing-slash or encoded path alias,
or public-ingress rules to make `/mcp` anonymous. Browser preflight follows the
platform's separate CORS handling. Native clients may omit `Origin`; any supplied
Origin must be a concrete origin admitted by the release's effective CORS policy.

`401` means authenticate with the client; `403` means inspect current release,
Environment, Origin or delegation permissions instead of bypassing them. A
startup/service-unavailable response does not prove a tool ran or failed safely.
Release access admits the connection; project tools still authorize the effective
person or workload using verified request context, including inside mounted ASGI
and concurrent/threaded tool execution. REST route dependencies alone do not
protect an MCP mount.
