---
name: a2a-communication
description: Discover an Environment-authorized Main Sequence Agent and intentionally use the canonical MCP Message-or-Task A2A tool family, with exact active caller-session proof for deployed Agent runtimes and a documented direct-runtime fallback.
---

# A2A Communication

Use this skill when another Main Sequence Agent is a better target for a
bounded request or when a request explicitly arrives through an A2A channel.
The workflow is language-neutral and does not require Python or the Main
Sequence CLI.

Main Sequence MCP advertises the canonical `a2a.send_message`,
`a2a.get_task`, `a2a.cancel_task`, and `a2a.wait_task` family. The sender
resolves platform objects and runtime access, calls the target runtime, and
returns a validated Message or durable Task without exposing transport
credentials to the model. A trusted harness may project canonical names into
its local tool namespace; host-specific projection and debugging belong to the
installed harness SDK rather than this platform protocol skill.
Agent discovery also advertises the fixed `agent.update_runtime` operation
when persisted evidence proves that the selected Agent needs replacement.

Streaming remains a direct-runtime concern. Durable Task state and dispatch are
Django-owned while the selected trusted runtime executes under canonical
authorization and the existing AgentSession lease. The explicit direct-runtime
construction below is the fallback for trusted A2A-capable hosts that do not
expose the MCP family or another constrained sender. It follows ADR-016 and
ADR-034.

## Preferred constrained sender

After fresh discovery selects a target, call `a2a.send_message` or the host-projected name from the
active tool catalog. Its input is a host-tool call, not the A2A wire envelope. A new target
conversation uses:

```json
{
  "agent_uid": "<selected-Agent.uid>",
  "handle_unique_id": "<stable-task-handle>",
  "message": "<bounded request>",
  "message_id": "<stable-message-id>",
  "response_kind": "message"
}
```

An existing target conversation uses:

```json
{
  "agent_uid": "<selected-Agent.uid>",
  "agent_session_uid": "<existing-target-AgentSession.uid>",
  "message": "<bounded request>",
  "message_id": "<stable-message-id>",
  "response_kind": "message"
}
```

For durable asynchronous work, use:

```json
{
  "agent_uid": "<selected-Agent.uid>",
  "handle_unique_id": "<stable-task-handle>",
  "message": "<bounded request>",
  "message_id": "<stable-message-id>",
  "response_kind": "task",
  "completion_policy": "poll"
}
```

`response_kind` is required for every controlled MCP call and has no default.
A direct `message` must be answered within the platform limit of 90 seconds,
counted from when the target receives it. The target then stops the turn and
the call fails with `a2a_message_limit_exceeded`; no setting extends the limit.
Use `task` for work that may take longer.
Message mode forbids `completion_policy`. Task mode requires
`completion_policy` equal to `poll` or `resume_caller`; `resume_caller`
requires verified active caller-session proof. For an initial send,
`agent_uid` is required and exactly one target-session selector is present:
`handle_unique_id` for a new conversation or `agent_session_uid` for an
existing one. The host tool owns session resolution and constructs the A2A
envelope shown under Request Construction.

Continue an interrupted Task without respecifying its fixed target:

```json
{
  "task_uid": "<AgentTask.uid>",
  "message": "<requested input or authorization result>",
  "message_id": "<stable-message-id>",
  "response_kind": "task",
  "completion_policy": "poll"
}
```

For an Agent-originated call, trusted harness code privately injects the exact active caller
session's UID, lease holder, and lease token through MCP request metadata. Django validates that
proof against the authenticated Harness Agent release and the current unexpired `runtime_run`
lease. Caller-session identity is not a model-visible tool argument. A human MCP caller needs no
Agent caller-session proof; Django creates or reuses a root target session for the authenticated
User.

For a human caller, an explicit existing `agent_session_uid` is usable only
when that User owns the session (or is a superuser). Organization administrators
may inspect another User's conversation through the canonical read tools, but
oversight access does not transfer session ownership or authorize sending a new
message with that User's provider credentials. An Agent view or edit grant alone
never authorizes cross-user conversation reads. Deployed Agent callers use exact caller-session proof for A2A provenance.
Verified delegation selects the person with their ordinary permissions, including
admin roles. Without delegation a workload uses its own rights. Invalid delegation
is rejected and never falls back to workload grants.

The trusted host attaches private proof under request `_meta` key
`mainsequence.ai/caller-session-proof/v1` for provenance. Explicit delegation
uses `mainsequence.ai/delegation/v1` with the same three session/lease fields;
both proofs must agree when supplied. Absence keeps workload authority; null
or invalid delegation is refused. There is no proof-request/requester tool
marker or second Agent-approved catalog. A2A provenance still identifies the
source conversation. Agent token resolution uses its ordinary endpoint/tool
permissions; the internal sender keeps its connection token out of message results.

`caller_session_proof_required` means the trusted runtime host failed to attach that private
provenance. The protected operation has not reached its target authorization or deployment logic.

When the selected discovery result has `runtime_update.state` equal to
`update_required` or `update_failed`, confirm the destructive operation and
call `agent.update_runtime` with exactly:

```json
{
  "agent_uid": "<selected-Agent.uid>"
}
```

The tool has no AgentSession UID, action, interaction revision, idempotency
key, service, branch, image, Environment, or deployment-strategy input. Django
reloads the Agent, revalidates current state and permission, and prepares or
reuses the canonical fenced operation. Private caller-session proof
authenticates a deployed Agent caller but never selects the deployment target.

## Canonical Flow

1. For a human or local caller, call `organization_environment.list`, present
   the visible choices, and ask which environment should bound the work. Skip
   this step only when the user already selected an environment or an authenticated
   deployed Harness Agent runtime uses its backend-derived target Environment.
2. Discover a bounded set of candidates with `agent.search`, passing the
   selected environment UID when it is model-visible.
3. Inspect the selected Agent with `agent.get` when more detail is needed.
4. Inspect `runtime_update`. When it is `update_required` or `update_failed`,
   call `agent.update_runtime` with only the selected `agent_uid`, then poll
   `agent.get` while it is `updating`. Continue only when it is `current`.
   Treat `unknown` as unknown and do not infer permission or currency.
5. Create or reuse its session with `agent.get_or_create_session`.
6. Resolve the session's current runtime endpoint and short-lived credential
   with `agent_session.resolve_runtime_access`.
7. Inspect `runtime_interaction.can_submit`. When the state is `waking`, call
   `agent_session.resolve_runtime_access` again after the bounded
   `retry_after_ms` interval until it becomes ready, terminal, or the host's
   wait deadline expires. Send only with the atomic URL, paths, and credential
   from the response where `can_submit=true`. Surface the backend-owned notice
   for a terminal or timed-out wait.
8. Read the selected Agent's `a2a_profile`, choose an advertised response kind,
   and send the versioned response-kind extension header.
9. Consume the returned `message` directly or follow the returned `task` using
   its documented lifecycle operations.

For the canonical MCP sender or a constrained host sender, steps 5 through 9
are one `a2a.send_message` operation after candidate selection. Durable Task
state is then read, waited on, or canceled through the corresponding Task tool.
The detailed steps remain the fallback implementation contract.

The `AgentSession.uid` is the durable conversation context. Runtime locations
and credentials are ephemeral and must be resolved again when they expire or
the runtime changes.

Treat the returned `rpc_url` as an opaque location. Do not construct it from a
service name, tenancy, environment, numeric identifier, or remembered
subdomain; the platform binds runtime access to the canonical `harness_agent`
ResourceRelease UID.

Treat each runtime-access result as one atomic bundle:
`{rpc_url, token, resource_release_uid}`. Never combine a remembered URL,
token, or service UID with any field from another resolution. Cache/group
identities must include Organization Environment UID, Agent UID, and release
or session UID; a display label or handle string is not an identity.
If the active Environment changes, resolve the Environment-owned Agent/session
and the entire runtime-access bundle again.

## Discovery

Agent discovery always has one Organization Environment boundary. Human/local
`agent.list` and `agent.search` calls require
`organization_environment_uid`; authenticated deployed Harness Agent runtimes
omit it because Django derives the Environment from the exact release target.
Both paths return only Code Repository Coding Agents
whose persisted CodeRepositoryBranches belong to that environment. For a human or
local MCP caller, call `organization_environment.list`, present each visible
name, required branch, production role, and public UID, and ask the user which
environment should bound the work. Continue limit/offset pagination until
`next` is null before presenting the choices. Resolve a user-supplied name
through that tool; never guess the UID or default to production. In an
authenticated deployed Harness Agent runtime, Django derives the Environment
and the host removes the argument from the model-visible tool schema; never call the
environment selector workflow, ask the user for it, infer it from a branch
name, or try to override it. If a runtime host supplies a redundant UID
assertion, it must equal Django's derived value. Schema hiding or host injection
is defense-in-depth and never replaces backend authorization.

Build a concise discovery query from:

- the capability needed;
- relevant domain and task boundaries;
- the expected response shape;
- any required operating constraints.

Use a bounded result limit. Prefer the highest-ranked suitable candidate, not
merely a familiar name. If the user asked only which agents are available,
report the candidates and stop without sending work.

Do not replace platform discovery with local prompt-file inspection.

Every `agent.search` and `agent.get` result includes an `a2a_profile` with:

- `response_kind_extension_uri`;
- `supported_response_kinds`; and
- `default_response_kind`.

Missing legacy profile data means Message-only support. Never infer Task support
from runtime routes, an empty answer, or prior knowledge of another Agent.

Every Agent discovery result also includes `runtime_update` with `state`,
tri-state `needs_redeploy`, and nullable `remediation`. `needs_redeploy=null`
is not false. Discovery is read-only and never updates, wakes, or creates a
session. When remediation names `agent.update_runtime`, use the Agent UID from
that same result; never invent another identifier.

## Session Reuse

Use a stable `handle_unique_id` for repeated work in the same target
conversation. Use a fresh task-specific handle for a genuinely new
conversation. A retry of the same get-or-create request reuses the same handle.
After a session is returned, reuse its public UID for later turns.

Handle uniqueness is `(agent, owner_user, handle_unique_id)`. The same User may
therefore have the same handle string on two distinct Environment-owned
Agents. Never collapse or reuse those handles across Agent or Environment
boundaries.

If the request originates from an existing caller session, the trusted host
supplies its exact active session as the parent provenance. The canonical MCP
sender carries this privately; a direct-runtime fallback supplies that verified
UID when creating the target session. A deployed Harness Agent runtime may
target only a backend-authorized Code Repository Coding Agent in its own
Organization Environment, and the parent session's Agent must be the calling
service's Agent. The host must not infer or broaden that target relationship.
The backend copies
`parent_session.created_by_user` into the child
session and its handle. Never provide or infer a replacement User. Parent
linkage proves the calling Agent, while the inherited owner preserves the User
whose request the chain is serving. Parent linkage is durable authorization
provenance for later delegated runtime-access and task operations; it does not
broaden the task or grant access outside that exact parent-child relationship.

For a continuation, the target session's immediate `parent_session_uid` must
still equal the exact active caller session. Service-level authorization or a
different concurrent session of the same calling Agent is not equivalent
provenance.

Each target runtime independently asks Django for the child session owner's
model-provider credential after exact runtime/session authorization. Never
read, serialize, forward, log, or place provider credentials in A2A message
parts, session metadata, handle metadata, or tool output. The runtime
credential authenticates the workload; explicit verified delegation selects
the effective person for authorization. Neither source-session ownership nor
stored requester attribution alone grants that authority. Runtime proof,
billing and audit retain the workload, and its creator is never a provider
credential fallback.

## Runtime Access Is Sensitive

The successful runtime-access result contains ephemeral sensitive data.

- Never echo, persist, cache beyond necessity, log, trace, or place the runtime
  credential in metrics or error details.
- Never send it to a different runtime or agent.
- Do not treat runtime access as authorization for any platform operation.
- If access is expired or unavailable, resolve it through the platform again
  instead of guessing an endpoint or token.

Treat `runtime_interaction.can_submit` as the sole new-message admission
decision. `is_ready` is routing health and `image_drift` is diagnostic input;
neither may override the interaction decision. When the interaction is
`waking`, resolve again after the bounded `retry_after_ms` interval until
ready, terminal, or the host wait deadline expires. When the structured block
is terminal, surface its returned notice.
When the structured block also returns `runtime_update` with the fixed
`agent.update_runtime` remediation, the caller may explicitly invoke that
Agent-scoped tool after confirmation. Never turn a session UI action into an
arbitrary deployment call, never update automatically inside A2A, and never
resend the blocked message automatically after an update.

The canonical MCP sender keeps the credential private. In the direct-runtime
fallback, the credential is visible only to the trusted host that makes the
request; keep it out of model-authored prose and reusable artifacts.

## Request Construction

Send a bounded request with a clear deliverable. When a machine-parseable result
is required, request a strict JSON object and specify its keys or schema.
Standard A2A responses expose message parts; do not request or depend on hidden
reasoning, thinking traces, tool traces, runtime paths, or transport internals.

Attachments may be sent as standard A2A file parts when the host and runtime
support them. Preserve filename and media type, enforce the current transport
size limit before sending, and do not encode local filesystem paths as a
portable contract.

Assign a stable message identifier before sending. If the exact same message
and attachments must be retried after a timeout or disconnect, reuse that
identifier. Use a new identifier when any logical request content changes.
This preserves request identity; it does not make direct `message` execution
durably idempotent. A lost direct response is ambiguous and a resend may execute
another turn. Select `task` when durable recovery is required.

For a normal request that is answered within the platform's 90-second Message
limit, select `message`. For longer or durable asynchronous work, select
`task` only when `supported_response_kinds` contains `task`. When the limit
expires the runtime stops the turn and answers HTTP `504` with status
`DEADLINE_EXCEEDED` and `ErrorInfo` reason `MESSAGE_RESPONSE_LIMIT_EXCEEDED`
(domain `mainsequence.ai`). Send the explicit
selection to the A2A v1 message endpoint returned in the atomic runtime-access
bundle:

```http
POST {rpc_url}{runtime_paths.a2a}/message:send
Authorization: Bearer {token}
Content-Type: application/a2a+json
Accept: application/a2a+json
A2A-Extensions: https://mainsequence.ai/a2a/extensions/response-kind/v1
```

`runtime_paths.a2a` must be present for this standard flow. Treat it as an
opaque backend-owned path. Never substitute `runtime_paths.chat`, a removed
`/api/a2a/sessions/.../runtime/chat` path, or a guessed `/api/a2a/v1` value.

The complete direct-Message request envelope is:

```json
{
  "message": {
    "messageId": "<stable-message-id>",
    "contextId": "<target-AgentSession.uid>",
    "role": "ROLE_USER",
    "parts": [
      {"text": "<bounded request>"}
    ]
  },
  "configuration": {
    "responseKind": "message"
  }
}
```

Activate it with an HTTP header whose value is the exact discovered extension
URI:

```http
A2A-Extensions: https://mainsequence.ai/a2a/extensions/response-kind/v1
```

For Task mode, set `configuration.responseKind` to `task` and independently set
the standard `configuration.returnImmediately` field to `true` for the
controlled asynchronous flow. `returnImmediately` does not select Task mode;
the response-kind v1 field does. Do not send `responseKind` on
`message:stream`, because streaming already selects a different result
contract.

### Requester and responder direction

The public A2A v1 boundary uses:

- requester -> `ROLE_USER` on the wire;
- responder -> `ROLE_AGENT` on the wire.

The runtime adapter maps public `ROLE_USER` and `ROLE_AGENT` to the internal Tau
binding directions `ROLE_REQUESTER` and `ROLE_RESPONDER`, respectively, and maps
them back for public responses. Do not send internal binding values to the public
endpoint or accept them as alternate response roles.

These values are transport direction, not principal identity. An Agent calling another Agent is
the requester for that exchange and sends `ROLE_USER`; it remains authenticated and audited
as an Agent through
`caller_kind=agent`, the caller Agent and service UIDs, and the authorized
parent-session UID. A human request uses the same requester wire direction but
has `caller_kind=user`. Never infer, assert, or override caller identity from
`message.role` or message metadata.

Request messages serialize the direction as `ROLE_USER`; response messages serialize it as
`ROLE_AGENT`. Do not emit or require the removed v0.3 `kind` discriminator on Message, Part,
or Task objects.

## Response Handling

- For `message`, require a valid `message` result and consume only documented
  response parts. The result must have the public responder role (`ROLE_AGENT`),
  and its `contextId` must equal the target AgentSession UID.
  An empty successful answer is a runtime contract failure.
- For `task`, require a valid `task` result and preserve its ID, context ID, and
  backend Task UID. Use `a2a.get_task`, `a2a.wait_task`, or
  `a2a.cancel_task`; the UID is an authorized selector, never bearer authority.
- Treat a response whose kind differs from the requested kind as a protocol
  error; never reinterpret it silently.
- Validate strict JSON before using it as structured input.
- Preserve the target session UID for the next turn in the same conversation.
- Treat a timeout or disconnect as an ambiguous outcome. A gateway `502` or
  `504` received after the message was sent is also ambiguous and returns
  `a2a_transport_outcome_ambiguous`. In direct `message` mode, do not
  automatically resend because the first turn may have executed.
  Do not create a new target session or blindly send a new logical message.
- Report target-agent failures without exposing credentials or internal
  transport details.
- A target that did not answer a `message` within the 90-second platform limit
  returns structured status 504 and `a2a_message_limit_exceeded`. The target
  stopped the turn; work it started may have run. Move the work to `task`
  instead of resending the same Message.
- A canonical runtime HTTP rejection returns structured status 502 and
  `a2a_runtime_rejected`, with the session/message and correlation identities.
  Its durable audit records that rejection; inspect the result instead of
  retrying the turn automatically.

## Delegation Boundaries

An orchestrating agent may discover candidates without confirmation. For a
user-originated request, obtain user confirmation before sending real work to
another agent unless the user's request already clearly authorizes that
delegation.

A runtime-owned child may make bounded A2A calls within the active
task scope. It must not use A2A to broaden the task, authorization boundary, or
code-repository context.

When responding to an incoming A2A request, answer agent-to-agent. Follow an
explicit output schema exactly; otherwise return concise machine-usable
content.

## Requester inheritance

An explicitly delegated A2A send carries its admitted person's identity and
original request time into the receiving Task or chat turn, for both `poll` and
`resume_caller`. A workload-only send carries no person, even when the source
has an active requester. Source-session provenance alone never selects a person.
Each executing Agent must be enabled before its runtime receives a person to
act for. Stored requester attribution remains available as an audit fact.

The trusted host preserves the platform-issued send reference on Task creation,
continuation or chat activation alongside the base signed caller assertion.
Django resolves the exact admitted send; neither model arguments nor workload
grants choose the person. Invalid or revoked admission fails without a workload
retry. A chain never restarts the 24-hour clock; stopping an ancestor session
withdraws the descendants' access.
