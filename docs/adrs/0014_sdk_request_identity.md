# ADR: Authenticate Every API Caller Through The SDK Request Identity

## Status

Amended by [ADR 0017](0017_encrypted_account_credentials.md) on 2026-10-24: the API no longer
writes Secrets; the platform calls it makes as its runtime principal now include the encrypted
key writes to MetaTables. Everything else below is unchanged.

Accepted on 2026-10-05. Jose approved this request-identity round for every workbench API; the
same change was made in mexicofundcompetition (`8567f17`), mainsequenceworkspacesapi, the CRM API,
valmer-connectors and sentinel. It amends how
[ADR 0011](0011_api_managed_alpaca_credential_secrets.md) identifies the human caller. The hosted
checks under [Verification](#verification) are still open.

## Context

PodDeploymentOrchestrator `3057764` ("Require application-installed caller identity without an
SDK dependency") removed the launcher's own caller resolver. The launcher no longer reads identity
headers or sets `request.state.user` and `request.state.user_uid`. Before it serves an application,
`serve_fastapi` calls `require_request_identity(app, public_ingress=...)`, which raises
`Application request identity is not installed` unless `app.state.mainsequence_request_identity`
is exactly:

- `installed`: `True`;
- `mode`: the deployment's caller mode, `assertion` when hosted;
- `public_ingress`: the routes of the deployment's `FASTAPI_PUBLIC_INGRESS`.

The API built its application at import time and declared nothing. The next deployment of
`alpaca-connectors-api` would therefore build a revision that fails at startup, while the previous
revision keeps serving. The API also read `request.state.user_uid` in four places, which nothing
sets any more:

- `POST /v1/accounts` and `PATCH /v1/accounts/{uid}` recorded it as
  `credentials_updated_by_user_uid` (ADR 0011);
- asset-registration operations stored it as `owner_uid` and returned an operation only to the same
  owner;
- `GET /v1/project-state/configuration` returned it as `request_user_uid`;
- discovery responses hashed it into their `ETag`.

A local run had no caller at all: every request was anonymous and those fields were null.

`mainsequence` 9.0.5 provides `mainsequence.server.fastapi.install_request_identity(app)`. It adds a
middleware that authenticates every request before routing, binds the caller for
`User.get_logged_user()`, and records the declaration the launcher checks. The backend gives each
FastAPI deployment its trust configuration (tdag-django `caller_runtime_environment` and
`run_knative_fastapi`): `MAINSEQUENCE_CALLER_AUTH_MODE=assertion`, the issuer, the caller-key URL,
the release UID as `APP_NAME`, the Environment UID, and the workflow's `public_ingress` as
`FASTAPI_PUBLIC_INGRESS`.

The vendored SDK `api_surfaces` skill (`.agents/skills/mainsequence/`, pinned to 9.0.5) still says
that the platform injects `request.state.user` and that no setup is needed. On that point it is
outdated. The SDK owns that directory, so it is not edited here.

## Decision

### 1. One installation per application

`create_app()` in `src/alpaca_connectors/api/app/main.py` builds the application:

1. registers the exception handlers and the response-header middleware;
2. mounts every router;
3. calls `install_request_identity(app)` once;
4. adds the CORS middleware when `ALPACA_CONNECTORS_CORS_ORIGINS` is set.

The module's `app = create_app()` is what `api/app/main.py` re-exports and the workflow deploys.
Each call returns a new application with its own installation, and a second installation on the
same application raises.

### 2. How a caller is verified

| Where | Caller proof | Rejected |
| --- | --- | --- |
| Hosted (any deployment marker) | The platform-signed caller assertion in `X-MainSequence-Caller-Assertion`, verified against the issuer, key URL, release and Environment the backend set on the deployment | 401 when missing, invalid, expired or for another release or Environment; 503 while the caller keys cannot be fetched |
| Local (no deployment markers) | `Authorization: Bearer <token>`, checked against `MAINSEQUENCE_ENDPOINT/api/v1/users/me/` | 401 when missing or rejected; 503 when the backend fails or `MAINSEQUENCE_ENDPOINT` is unset |

- `X-User-UID` alone never names a caller. Locally, one that disagrees with the token is rejected.
- A request carrying `X-User-ID` is rejected.
- The SDK removes `Authorization` before the application sees the request.
- A hosted deployment cannot select local mode, and a deployment marker without trust
  configuration fails application creation.

### 3. No public routes

The workflow declares no `public_ingress`. Every route therefore needs a caller, including
`/health`, `/openapi.json` and `/docs`. Hosted readiness uses the launcher's own
`/ms-health-deployment`, which the launcher answers outside the application. `OPTIONS` requests
pass without a caller so that CORS preflights work; the API has no `OPTIONS` route, so they never
reach a handler.

### 4. One caller lookup

Routes read the caller only through `alpaca_connectors.api.app.identity`:

- `caller_uid()` returns `User.get_logged_user().uid`, or answers 401 if no caller is bound;
- `bound_caller_uid()` returns it or `None`, for the discovery `ETag`.

The four uses keep their meaning with the verified caller: the credential author on account create
and rotate, the owner of a registration operation, `request_user_uid`, and the discovery `ETag`.
No route reads `request.state`, an identity header, or the process's own SDK user, and nothing
replaces a caller the SDK did not verify.

### 5. CORS outside the identity

The application's CORS middleware is added after the identity, so it is the outer one. A preflight
is answered without a caller, and a browser on an allowed origin can read the 401 or 503 the
identity sends. A deployment has the same order, because the launcher adds its own CORS
middleware, built from the workflow's `cors_allowed_origins`, outside the application.

### 6. Authorization unchanged

The identity says who called, not what the caller may do. Any authenticated caller may use every
route, as before; who may call the release is still set by its access policy (ADR 0011).
Registration operations stay visible only to the caller who started them. Calls the API makes to
Main Sequence (Secrets, MetaTables, Jobs) still run as the API's runtime principal: the caller is
recorded, not impersonated.

## Consequences

- The launcher accepts the application, so the next push can deploy a new revision.
- Every client must present a caller.
  - Inside Command Center, the host's authenticated call through the platform gateway carries the
    caller assertion; the site needs no change for that path.
  - A local API needs `Authorization: Bearer $(mainsequence auth token)`, with the CLI and the API
    using the same `MAINSEQUENCE_ENDPOINT`. Tokens are short-lived.
  - The sibling static site's standalone development mode (`VITE_API_BASE_URL`, used by the VS
    Code compound and `scripts/dev_stack.sh`) sends no token, so it gets 401 until the site sends
    one. The site repository is not changed here.
- Local runs now record the token's user as credential author and operation owner instead of null.
  An operation that a local run created earlier without an owner can no longer be polled. These
  rows are transient progress records.
- `/health` answers 401 without a caller.
- The OpenAPI document is unchanged.

## Verification

- **Tests and lint.** `uv run --offline pytest` passes 398 tests and 70 subtests, up from 353.
  `uv run --offline ruff check` passes. `tests/test_api_request_identity.py` covers:
  - the declaration: local and assertion modes, assertion mode from the deployment markers alone,
    a deployment marker without trust configuration, no local mode when hosted, public ingress
    equal to `FASTAPI_PUBLIC_INGRESS`, a workflow without `public_ingress`, one installation per
    application, the deployed entry point, and the middleware order;
  - caller resolution in both modes, including a spoofed `X-User-UID` and the removed token;
  - rejection, before any handler runs, of missing, forged, foreign, expired, numeric and
    header-only callers in both modes, 503 when caller keys or the backend are unavailable, and
    401 for an anonymous request on every route;
  - with two callers in each mode: the credential author on account create and rotate, operation
    ownership, `request_user_uid` and the per-caller discovery `ETag`;
  - CORS preflights without a caller, and CORS headers on the identity's 401.

  The existing API tests call the application as an authenticated local caller.
- **OpenAPI.** `app.openapi()` is byte-identical to `87824ee`.
- **The platform launcher, simulated deployment.** PodDeploymentOrchestrator's own code at
  `9d2c0d4`, which contains `3057764` and whose launcher modules have not changed since, ran under
  the environment the backend gives this release:
  - `FASTAPI_FACTORY_PATH=api.app.main.py` and `FASTAPI_PUBLIC_INGRESS=[]`;
  - assertion mode with test issuer, caller-key URL, release and Environment values;
  - the workflow's CORS origins and `ALPACA_CONNECTORS_CORS_ORIGINS`.

  Only caller-key discovery used a test key, and `uvicorn.run` captured the application instead of
  binding a port. Results:
  - On `87824ee`, `require_request_identity` and `serve_fastapi` both raised
    `Application request identity is not installed`. With this change both pass.
  - Through the launcher-wrapped application, requests with no assertion, `X-User-UID` only, a
    malformed token, an unpublished key, another release, another Environment, an expired
    assertion, or `X-User-ID` got 401 on `/health`, `/v1/project-state/configuration` and
    `/v1/accounts/discovery`.
  - A valid assertion got 200 on all three. The configuration reported the signed user even with
    a spoofed `X-User-UID`, and the launcher's request log recorded that user.
  - `/ms-health-deployment` answered 200 without an assertion. A preflight from a
    `*.site-dev.main-sequence.app` origin got 200, and the anonymous 401 carried that origin's
    CORS header.
- **Local, against the development backend.** The API ran under uvicorn as `scripts/dev_stack.sh`
  starts it, on the local MetaTables runtime, with `MAINSEQUENCE_ENDPOINT=http://127.0.0.1:8000`.
  - With a `mainsequence auth token` Bearer, `/health`, `/v1/project-state/configuration`,
    `/v1/accounts` and `/v1/accounts/discovery` answered 200. `request_user_uid` equalled the UID
    the backend's `users/me` returned for that token.
  - These got 401: no token; `X-User-UID` alone; a token the backend did not issue; a Basic
    credential; a Bearer with a conflicting `X-User-UID`; a Bearer with `X-User-ID`; and
    `/openapi.json` without a token.
- **Not verified:**
  - the hosted rollout: the push (disabled during the mt014 wave), the image build, the migration
    Job, a ready revision, and the gateway forwarding caller assertions to it;
  - the Command Center site against the new revision, and the static site's standalone mode with a
    token.
