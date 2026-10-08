---
name: model-provider-credentials
description: Discover model providers and guide built-in sign-in, status, cancellation and revocation through Django; explain the approved provider-sharing trust policy without receiving provider credentials through MCP.
---

# Model Provider Credentials

Use the platform tools as the source of truth. Do not guess provider or model
identifiers and do not publish a fixed provider/model table.

Django Agents owns the catalog, durable sign-in attempts, provider callback
and exchange, credential activation, refresh, and non-secret projections. MCP
dispatches directly to those canonical operations. Catalog and sign-in tools
never require or return an Agent, `AgentSession`, agent deployment, runtime URL,
or runtime-access credential.

## Discover providers and models

1. Call `model_provider.list` before recommending a provider or model. It accepts
   optional `organization_environment_uid`; omit it only when the user's existing
   active or sole accessible Environment provides the context.
2. Distinguish built-in rows (`known=true`) from Organization custom rows
   (`known=false`) in that same result. Do not make or recommend a second
   provider-discovery call.
3. Present relevant returned provider fields: `display_name`, `auth_methods`,
   `known`, `enabled`, `credential_status`, `authenticated`,
   `sign_in_available`, and `default_model`.
4. Use only the concrete returned `models`. A model includes its identifier,
   display name, API protocol, supported input kinds, reasoning flag, thinking
   levels, context window, and maximum output tokens.
5. For built-ins, explain that `known` and `enabled` are catalog/product state
   while `authenticated` is User credential state. For an Organization custom
   row, `authenticated=true`, `credential_status=missing`, empty
   `auth_methods`, and `sign_in_available=false` mean no per-User sign-in is
   required; they are not custom-provider lifecycle state.
6. The general catalog does not claim that a specific runtime supports, can
   select, or can execute a model.
7. If the user did not name a provider, ask them to choose from the live
   result. Do not substitute another provider silently.

The `schema_version` identifies the shared response contract.
`catalog_digest` identifies only the immutable built-in catalog; it is not a
revision or digest for Organization custom rows. Neither is runtime
compatibility or execution proof.

## Read credential state

Call `model_provider_credential.status` with optional `provider`, `custom_id`
and `organization_environment_uid`. For a named configuration, retain all three
through this workflow. Status is non-secret and never hydrates the stored credential.
Use `configured_credentials` from the catalog for names and safe record identity;
do not duplicate the provider definition for each name.

`provider` names the integration; `custom_id` selects one configured credential
in the chosen Environment. Show its name, owner (`user_uid`), Environment and
returned credential UID. An explicit name selects the caller's own record of
that name first, otherwise it must match one authorized record: missing, revoked
or ambiguous selections do not fall back to another name.
For a new sign-in, an explicitly requested unused name can reserve a new record.
Never substitute a new name merely to work around a permission error.

Without a name, reads/execution prefer the User's original owned record, then
the earliest shared record; legacy sign-in/revoke writes address the first owned
record. An existing session retains its exact credential UID when selection is
omitted. A name containing `default` is just a name. A use/view grant permits
runtime delivery, not sign-in changes or revocation; those require edit access.
If the Environment is unresolved, select one through the Environment workflow.
Do not cross a deployed runtime's target-derived Environment.

## Sign in

1. Require the selected provider to be present and enabled in the latest
   `model_provider.list` response.
2. If `known=false`, explain that authentication is managed on the
   Organization custom provider. Do not call User credential status, sign-in,
   cancellation, or revoke tools for it. MCP does not expose custom-provider
   mutation or secret capture.
3. Inspect the selected configured credential's status. An authenticated
   provider-level catalog row may describe a different name. Skip sign-in only
   when the selected record is already usable and no reconnection was requested.
4. If `sign_in_available=false`, explain that interactive sign-in is not
   currently configured. Do not call start.
5. Call `model_provider_credential.sign_in_start` with the exact returned
   `provider`, selected `custom_id` when named, and the resolved
   `organization_environment_uid`. Never silently drop a selected name.
6. Retain the returned attempt `uid` only for this workflow. Check its safe
   `credential_uid`, `custom_id` and Environment against the intended selection.
7. Follow `next_action`:
   - `open_url`: show the exact `authorization_url`, optional `user_code`, and
     instructions, then ask the user to complete provider authorization; or
   - `none`: inspect the attempt status before acting again.
8. Call `model_provider_credential.sign_in_get` with
   `attempt_id=<returned uid>` until the attempt is terminal. Django owns the
   browser callback and advances due device-code polling durably.
9. Treat `completed`, `failed`, and `cancelled` as terminal. Report
   `error_code` when present, without inventing provider details.
10. After completion, call `model_provider_credential.status` again with that
    same provider, name and Environment, and report only the non-secret result.

Repeated starts for the same User and configured credential reuse its active
attempt. Different names can sign in independently. Another editor's active
attempt on a shared record is refused; do not cancel or replace their attempt.
After an ambiguous result, inspect canonical state before starting again.

There is no manual-continuation tool. Never ask the user to paste a callback
URL or authorization code into the conversation or an MCP tool.

## Secret boundary

Never ask the user to paste an API key, access token, refresh token, password,
client secret, PKCE verifier, device authorization secret, callback value, or
complete credential into the conversation or an MCP tool.

Django exchanges provider authorization and persists the final credential.
MCP and the calling agent never receive the final credential. Do not call or
simulate hydrate, flush, exchange, refresh, or a generic HTTP operation.

## Explain provider-sharing trust

ADR-0049's provider-sharing trust policy was approved by the owner on 2026-10-05.
The revised feature names and shares existing configured records using
`custom_id`; it does not add account/subscription models. Sign-in start and revoke
accept optional `custom_id` and `organization_environment_uid`. Names are scoped
to the Environment. Existing sessions retain their exact credential UID.
Use live tool discovery and never invent a sharing tool or a successful grant.
The Command Center provider details screen uses the existing sharing controls;
this MCP skill does not expose credential delivery or generic HTTP tools.

Before helping with a supported sharing action, explain:

> Sharing this configured provider lets recipients receive and copy its
> credentials in their own runtime, including local Tau. Only share with people
> and workload operators you trust. Their usage counts against the provider quota
> or billing associated with these credentials. Removing access stops future
> credential retrieval; credentials already received may work until they expire
> or are revoked at the provider. Organization admins don't need a share: they
> can receive and use the credentials of any configured provider in the
> Organization, and their usage counts against it too. An admin with none of
> their own for a provider gets the first one added in that Environment.

This applies to people, teams and workload Users, including their runtime/code
operators. Organization admins hold the same access without a share. Existing Environment, object and session authorization still applies.
Sharing one configured record does not grant access to other credentials for
the same provider. There is no separate local-use permission.

A workflow file can share a configured record with its own workload's User by
declaring it under `access.model_providers` (code-repository-workflows skill,
Declared Access): a built-in provider's credential by `provider` and
`custom_id`, a custom endpoint by `provider` alone. Give the same disclosure
before helping write that declaration, and add that every later push to the
branch keeps the share.

Reuse the user's existing authorization and the normal sharing flow. This
policy does not add a confirmation requirement. Command Center must show the
disclosure before Share is submitted and retain it in the configured provider's
details. Identify the selected name, owner, Environment and recipient.

The recipient's authorized runtime can receive credentials; MCP results,
model-visible conversations, logs and ordinary read responses cannot. Never
fetch keys to demonstrate the policy or claim unsharing recalls delivered tokens.
The canonical disclosure is in
`docs/agents/providers/model_provider_credentials.md`; its owning ADR remains
`docs/platform/adr/adr-0049-shareable-model-provider-accounts.md`.

## Cancel or revoke

Use `model_provider_credential.sign_in_cancel` with only the exact
`attempt_id` the user wants to stop. Django derives its provider and requires
the attempt to belong to the authenticated User.

Before `model_provider_credential.revoke`, identify the selected name, owner and
Environment, explain that it invalidates that stored credential for its users,
and obtain explicit confirmation. Call it with the exact `provider`, selected
`custom_id`, resolved `organization_environment_uid` and a concise `reason`.
For an explicitly intended legacy default, omission of `custom_id` preserves
the existing first-owned-record behavior; it never means all credentials.
Use access alone cannot revoke a shared record. Reuse the canonical result;
do not delete records or silently retry against another configuration.


## Recorded calls from application code

The same catalog and stored credentials support the Main Sequence SDK's
`InferenceClient` for direct recorded model calls. Inference owns those records;
an AgentSession is not required. The internal consumer authorizes the effective
User and exact Environment, with optional named credential selection. A workload
calling as itself needs a shared configured provider; it never borrows its
creator's credentials. Public AgentSession hydration and MCP credential
boundaries are unchanged; MCP does not execute these completions or return secrets.
Provider storage options never disable Main Sequence history, which remains until
explicitly deleted. See `docs/inference/recorded_conversations.md` for the contract.
