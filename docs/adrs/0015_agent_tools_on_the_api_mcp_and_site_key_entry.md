# ADR: Agent Tools On The API's MCP, And Alpaca Keys Entered On The Site

## Status

Amended by [ADR 0017](0017_encrypted_account_credentials.md) on 2026-10-24: the keys a person
enters on the site are stored encrypted on the account row, not as managed Secrets, and the
Agent's `secret_references` operation is gone; registration from Secret names imports the keys
once. The key-request flow and the identity rules below are unchanged.

Accepted on 2026-10-08. Amends the "Tau tools and agent sessions" surface of
[ADR 0011](0011_api_managed_alpaca_credential_secrets.md).

On 2026-10-11 the API began returning this Agent to the Alpaca site, as the CRM API returns its
assistant. The API and the Agent declare access to each other in the workflow's
`access.resources`: `view` under workflow API 2.3.0, `user` since the move to 3.0.0 on 2026-10-21.
`deploy_agent` needs `deploy_api`. `GET /v1/project-state/configuration`
returns `agent_uid` and `organization_environment_uid`. The API finds the Agent when the request
arrives, by the card's name, in the Environment and on the branch that the SDK context resolves.
The site's build holds neither UID ([API](../api.md#project-state-and-the-sites-agent)).

## Context

ADR 0011 lets the Alpaca site register an account from submitted API keys: the API validates them
against Alpaca and stores them as application-managed Main Sequence Secrets. The Agent could not do
the same. Its tools took Secret names only, because a value given to an Agent tool is written by the
model and is kept in the conversation's history. People who wanted the application to store their
keys had to leave the conversation and find the site on their own.

The Agent's tools also ran inside the Agent's own process, against the project services, with the
Agent's own platform access. Every person using the Agent therefore reached what the Agent could
reach, not what they could. The Agent's declaration used the `code_repository_coding_agent` kind,
which the platform no longer accepts, so the Agent was not deploying.

## Decision

### The project tools run in the API, behind its MCP endpoint

- The API serves an MCP server at `/mcp` (`alpaca_connectors.api.app.mcp_server`), installed by
  `create_app()` after the request identity. It serves the `alpaca_connectors.agent_tools`
  catalog with the same names, descriptions, and input schemas. Each tool runs the same services as
  the REST routes, in a worker thread that keeps the call's verified identity. Arguments are
  validated against the tool's schema, and errors never quote them.
- The workflow declares the API with `mcp_enabled: true` under the key `alpaca`. The key is also
  the Agent's tool prefix, so the Agent sees `alpaca__list_tools` and `alpaca__call_tool`. Tool
  prefixes must match `^[a-z][a-z0-9_]{0,39}$`, which the previous key `alpaca-connectors-api` did
  not.
- The Agent is a `harness_agent` in the same workflow file, served by `api/agent/main.py`
  (`ms_tau_sdk.create_app()`), with `mcp_applications: [{resource: alpaca}]` and
  `acts_for_requester: true`. Its calls carry the person it serves; the in-process
  `.tau/extensions` tools are removed.
- Authorship and ownership in the API follow one acting user: the person on a call an Agent makes
  for them (`User.get_requester()`), otherwise the caller. The two are never combined, and a
  person's UID is never read from a body, header, or tool argument.

### Keys are entered on the Alpaca site, for a request the Agent starts

- The new `alpaca_account_key_request` tool creates an `AccountKeyRequest` for the person the call
  works for: `register` (with Alpaca live/paper mode and an optional account name) or `rotate` (for
  one account). This is not an Organization Environment selector. It returns the site link
  (`ALPACA_CONNECTORS_SITE_URL`) and instructions for the
  person. It accepts no key values.
- Command Center opens the site at its navigation link and passes nothing from its own URL into
  the site, so the link cannot carry the request. Instead, the site's Accounts page lists the
  signed-in person's own pending requests (`GET /v1/accounts/key-requests`) and offers each one's
  form with its settings filled in.
- The person types the keys there. The site sends them to `POST /v1/accounts` or
  `PATCH /v1/accounts/{account_uid}` with the request's `key_request_uid`. Before calling Alpaca
  or writing any Secret, the API checks that the submitter is the person the request belongs to,
  that it is pending and unexpired (one hour), and that its action, environment, and account match.
  It then stores the keys exactly as ADR 0011 describes and completes the request with the account
  UID.
- The Agent reads the outcome with the tool's `status` operation; `cancel` withdraws a request.
  Another person's request reads as missing.
- Requests are stored in the new `alpaca_connectors__account_key_request` table (revision `0017`).
  A request holds no credential value, Secret name, or Secret UID.

## Consequences

- Through the Agent, a person can register an account or replace its keys, live or paper, without
  the keys ever entering the conversation.
- Agent-to-API calls carry the person's delegated identity and are subject to application access;
  API authorship and ownership use that verified person. Ordinary API-to-platform and MetaTables
  calls still use the API workload principal and its grants, not automatically the person's
  credentials. This distinction does not imply per-person row filtering for every resource (see
  [Agent identity and authorization](../agent.md#identity-and-authorization)).
- Renaming the API's workflow key recreates its release: it gets a new release UID, URL, and
  workload User. The workflow therefore declares the access the API needs (since workflow API
  3.0.0: branch `editor` to run the bars Job another workflow file declares, `runner` on the signal
  and portfolio Jobs, and `reader` on the MetaTables repository's branch), and the Alpaca site
  refers to the API by repository and key instead of a pinned UID. Access granted to the old release by
  hand, such as external Secrets an account selected by name, must be granted to the new workload
  User again. Managed Secrets the previous release created must be shared with it. The
  `Production` Environment had no Alpaca tables registered when this was decided, so it had no
  accounts or managed Secrets to carry over.
- An account can still be registered from existing Main Sequence Secrets by name, through
  `alpaca_manage_account` or the CLI. Neither accepts key values.
