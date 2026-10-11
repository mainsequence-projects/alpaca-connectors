# FastAPI Backend

## Contract

The FastAPI layer validates transport input and calls the same reusable services as the CLI.
Provider calls, MetaTable operations, holdings transformation, and market-data updates are not
implemented in route bodies.

Collections return:

```json
{
  "items": [],
  "pageInfo": {
    "pageIndex": 0,
    "pageSize": 25,
    "totalItems": 0,
    "hasNextPage": false,
    "hasPreviousPage": false
  }
}
```

Selectable collections expose a separate `/discovery` response compatible with the installed
resource adapter contract. Discovery responses are private, revalidated, and vary on delegated
authorization and Resource Release headers.

Bulk actions advertised by discovery use full app-relative `endpoint` and `preflight_endpoint`
paths, such as `/v1/accounts/actions/remove`. The resource adapter sends both requests to those
exact paths with the action's `method`; it does not resolve them against the collection path. The
offline API tests require every advertised path and method to match a registered route.

The provider-neutral HTTP contracts come from `msm.api.http` in `ms-markets>=1.0.14`:
collection pagination, resource discovery, bulk-action execution and preflight, sanitized generic
errors, and observable-operation wire models. Alpaca-specific resource declarations, option
parsing, provider-error classification, and business services remain in this repository. Asset
registration operation rows also remain in the project-owned MetaTable so polling survives API
process restarts; the shared in-memory operation repository is intentionally not used.

## Resources

| Resource | Main endpoints |
| --- | --- |
| Project State | `GET /health`, `GET /v1/project-state/capabilities`, `GET /v1/project-state/configuration` |
| Assets | `GET /v1/assets`, `GET /v1/assets/{asset_uid}`, synchronous registration plan/execute, and observable registration operations |
| Accounts | `GET/POST /v1/accounts`, `GET /v1/accounts/secret-references`, `POST /v1/accounts/registration/preflight`, `GET/PATCH/DELETE /v1/accounts/{account_uid}`, refresh and bulk actions; the signed-in person's key requests under `/v1/accounts/key-requests` |
| Holdings | list/get and capture under `/v1/accounts/{account_uid}/holdings`; `/latest` lists only the newest immutable snapshot |
| Universe Sources | Explicit extraction-configuration CRUD and read-only source preview under `/v1/universe-sources` |
| Universes | Create/list/get/update registered `AssetUniverse` rows; list linked category Assets at `GET /v1/universes/{universe_uid}/assets`; run, activate, deactivate, and confirmed delete actions under `/v1/universes` |
| Market Data | configuration CRUD and resolve/update actions under `/v1/market-data/bar-configurations`; dataset list/get and bounded observations under `/v1/market-data/datasets` |
| ETF Signals | signal configuration CRUD under `/v1/signal-jobs`; run, pause, resume, reconcile, and JobRun-history actions on the shared signal Job |
| ETF Portfolios | configuration CRUD under `/v1/portfolio-configurations`; reusable calendar-event policies under `/v1/portfolio-rebalance-configurations`; run and JobRun-history actions on the shared portfolio Job |
| Operations | poll an accepted Alpaca bars, ETF signal, or ETF portfolio JobRun under `GET /v1/operations/job-runs/{job_run_uid}` |

Market-data writes are configuration-oriented. Create one stored definition with exactly one
source (`assets`, `universe`, or `account_holdings`), review it with the `resolve` action, and run it
with the `update` action. Neither request accepts a dataset UID. The response reports the derived
output MetaTable only as diagnostics.

The bar-configuration `update` action is asynchronous. It performs a read-only configuration
preflight, submits the branch-owned `Alpaca Bars Update` Job with exactly
`--configuration-uid <UUID>`, and returns `202 Accepted` with a JobRun status URL. The HTTP process
does not contact Alpaca or write bars. Poll responses are never cached and expose only sanitized
failure information plus the platform log URL.

`POST /v1/universes` requires a name, ETF ticker, and explicit holdings source URL. It stores or
resolves that exact source, creates an empty category, and creates an `AssetUniverse` with explicit
source and category relationships. The identities are returned as `uid`, `source_uid`, and
`asset_category_uid`; creation does not extract holdings. Run is a separate action on the registered
Universe UID and requires `options.account_uid` as execution context. Its preflight extracts the
source and plans provider-native asset registration through the selected account. Missing
Main Sequence assets are work to perform, not blockers. Execution registers them first and replaces
membership only after every constituent succeeds. Symbols that Alpaca cannot resolve are returned
as blockers and leave membership unchanged. No ticker, provider, source, category, or account is
inferred or persisted on the Universe.

`GET /v1/universes/{universe_uid}` returns the linked category identity and an aggregate Asset
count without embedding all members. `GET /v1/universes/{universe_uid}/assets` reuses the standard
paginated Asset response and discovery contract, scoped to that linked category. The Universe
collection and detail endpoints do not perform one backend request per Asset. Universe execution
publishes the extracted weights through the canonical `SignalWeightsStorage`. The linked category
records membership only; weights do not become category-membership columns.

## Signal And Portfolio Jobs

Signal and portfolio configurations run on the two Jobs declared in
`.mainsequence/workflows/alpaca-connectors-api.yaml`, one per kind
([ADR 0013](adrs/0013_workflow_declared_configuration_jobs.md)).

- **Requests.** Create and update requests accept `scheduled` (default true). It decides whether
  the kind's daily scheduled run includes the configuration. A request that still sends schedule or
  compute settings (a nested portfolio `job`, or the flat signal schedule and compute fields) gets
  422 naming the workflow file, because they belong to the declaration. Signals no longer accept
  `enabled`; send `scheduled`.
- **Responses.** They report `scheduled` and the kind's Job as `job_uid` and `job` (its schedule,
  compute and image). Both are null until a push creates the Job on the branch. A signal's
  `lifecycle_state` is `provisioning` until then, and afterwards `ready` or `paused`.
- **Runs.** A run returns 202 with the JobRun UID and a `status_url`. It starts the kind's Job with
  `--configuration-uid <uid>` and is refused while that configuration's newest run is pending or
  running. Run lists and `/v1/operations/job-runs/{job_run_uid}` find a configuration's runs by that
  argument and return it as `command_args`.

## ETF Signals

`POST /v1/signal-jobs` creates one durable signal configuration from one Universe and one registered
Alpaca account; it does not touch the platform. `universe_uid` defines signal identity.
`account_uid` is runtime configuration and never becomes part of the final signal UID or
`SignalWeightsStorage`. A run performs one Universe extraction, materialization and signal update.

Lifecycle routes are:

```text
POST /v1/signal-jobs/{configuration_uid}/actions/run
POST /v1/signal-jobs/{configuration_uid}/actions/pause
POST /v1/signal-jobs/{configuration_uid}/actions/resume
POST /v1/signal-jobs/{configuration_uid}/actions/reconcile
GET  /v1/signal-jobs/{configuration_uid}/runs
GET  /v1/signal-jobs/{configuration_uid}/observations?limit=100
```

`pause` and `resume` set `scheduled`. `reconcile` only ensures the signal's ms-markets metadata.
The list filters on `scheduled` and orders by `name` or `updated_at`. Deleting a configuration
removes only its row; prior signal observations and the shared Job's runs remain.

The observations endpoint resolves the signal UID from the stored configuration, selects the
latest distinct observation timestamps from canonical ms-markets `SignalWeightsStorage`, and
returns all constituent rows for those timestamps as a compact aligned payload. It never accepts an
arbitrary signal UID and performs one set-based backend query rather than one request per Asset.
The limit is between 1 and 100 and defaults to 100. Timestamps are returned chronologically and each
Asset carries one aligned weight per timestamp; the frontend projects that payload with timestamps
as rows and Assets as columns. Because signal updates publish complete frames, an absent Asset row
is represented as zero while an explicit stored null remains null.

## ETF Portfolios

`POST /v1/portfolio-configurations` creates one durable calculation definition; it does not touch
the platform. The definition references an existing ETF Signal Configuration, Bars Configuration,
and Rebalance Configuration. The supported strategy is `CalendarEventSignal`, backed by persisted
market-session events; valuation uses daily persistent `InterpolatedPrices`, forward-fill
interpolation, and a bounded staleness policy.

The Portfolio Configuration MetaTable stores no schedule, compute, image, deployment, Environment,
or JobRun state; its only operational field is `scheduled`. `PATCH {"scheduled": false}` pauses a
portfolio without validating its references. A run also needs an allowed preflight: its linked
configurations must exist and be enabled, and its signal must have published observations.

```text
GET|POST /v1/portfolio-configurations
GET|PATCH|DELETE /v1/portfolio-configurations/{configuration_uid}
POST /v1/portfolio-configurations/{configuration_uid}/actions/run
GET  /v1/portfolio-configurations/{configuration_uid}/runs

GET|POST /v1/portfolio-rebalance-configurations
GET|PATCH|DELETE /v1/portfolio-rebalance-configurations/{configuration_uid}
```

There is no Environment request field. A run selects its configuration with
`--configuration-uid <uid>` and updates persistent interpolation, calendar events, rebalance state,
executed weights, and valuation without rerunning the Signal or raw Bars producers. Rebalance rows use actual configured
market-event timestamps and select the latest signal observed at or before each event. The result
is still an analytical reconstruction rather than guaranteed point-in-time ETF replication.

The configuration detail `GET` accepts `observation_limit` from 1 through 5,000 (default 2,500). It
returns resolved Signal, Universe, Alpaca account, Bars, Rebalance, Job, and canonical
portfolio/calendar summaries plus the requested latest values from `PortfoliosStorage`. The history
query is scoped by the canonical Portfolio unique identifier and runs only when a consumer opens
that configuration; API consumers do not need to interpret or display related resource UIDs.

The same response includes window-scoped performance statistics calculated from those canonical
daily values with `empyrical-reloaded`: total and annualized return, annualized volatility, Sharpe,
Sortino, maximum drawdown, Calmar, best/worst daily return, and the positive-period ratio. The
response records the exact observation count, date window, 252-period annualization factor, and 0%
risk-free-rate assumption. Alpha and beta are not reported because this workflow has no configured
benchmark. If the requested window is shorter than the stored history, the response marks it as
truncated rather than presenting the statistics as since-inception results.

## Observable Asset Registration

Browser clients should start registration work through:

```text
POST /v1/assets/registration/operations
GET  /v1/assets/registration/operations/{operation_uid}
```

The start request contains `action` (`plan` or `execute`) and the validated registration request.
That request requires `account_uid`; the backend uses the selected registered account's stored
Main Sequence Secret references for Alpaca access and never accepts credential values.
The API returns `202 Accepted` with a stable operation UID and an ordered step list. Clients poll the
GET route using the returned `poll_after_ms` value. Poll responses use `Cache-Control: no-store`.

Operation states are `queued`, `running`, `succeeded`, and `failed`. Step states are `pending`,
`running`, `succeeded`, `failed`, and `skipped`. Every transition records timestamps and a user-safe
message. A successful operation includes the normal plan or execution result. A failed operation
retains only the sanitized public error contract; provider exception internals are not recorded.
Alpaca catalog loading, Alpaca UUID validation, and optional OpenFIGI enrichment are separate
steps. The error code and message identify credential lookup, Alpaca, or Main Sequence as terminal
failures. OpenFIGI failures and unmatched symbols are recorded as warnings and do not fail the
operation.

Progress records are persisted in the project-owned
`alpaca_connectors__asset_registration_operation` MetaTable and belong to the verified caller who
started them ([Authentication](#authentication)); polling another caller's operation answers 404.
The existing synchronous plan and execute routes remain available for non-interactive callers.

The record is durable, but execution currently runs as a FastAPI background task rather than a
platform Job. A process termination can therefore leave an accepted operation in `queued` or
`running`; clients must use a bounded polling policy. Restart recovery is intentionally not claimed.

## Credentials And Identity

Account registration, preflight, and `PATCH` requests carry a `credentials` object discriminated
by `source` ([ADR 0011](adrs/0011_api_managed_alpaca_credential_secrets.md)):

- `{"source": "managed", "api_key": "...", "secret_key": "..."}`: the API validates the values
  against Alpaca and stores them in the application-managed Secrets
  `ALPACA_CONNECTORS__<Account.unique_identifier>__API_KEY` / `__SECRET_KEY`. Both fields are
  `writeOnly` `password` strings in OpenAPI and are never returned. The static site uses only this
  shape.
- `{"source": "external", "api_key_secret_name": "...", "secret_key_secret_name": "..."}`:
  existing Secrets the API only reads.

`PATCH` without `credentials` changes only the name or active state and never reads credentials.
Rotation must authenticate as the registered Alpaca account. Preflight reports the managed Secret
names and whether each would be created or updated, and writes nothing. Responses expose
`credential_source`, both Secret names and UIDs, and `credentials_updated_at`/`_by_user_uid`; never
values or the key fingerprint. `DELETE` reports `deleted_secrets` and `warnings`.

Alpaca rejecting the keys returns `400 alpaca_credentials_rejected` without provider text. Every
`422` response lists only `loc`, `msg`, and `type`: FastAPI's default body echoes the rejected
`input`, which could be a credential.

`GET /v1/accounts/secret-references` returns only Main Sequence Secret names visible to the API
runtime, for clients that use the `external` shape. It never serializes Secret values.

### Account Key Requests

The Agent never receives key values. Its `alpaca_account_key_request` MCP tool creates a pending
request for the person it serves: `register` (environment and optional name) or `rotate` (one
account) ([ADR 0015](adrs/0015_agent_tools_on_the_api_mcp_and_site_key_entry.md)). The site then
works only with the signed-in person's own requests:

```text
GET  /v1/accounts/key-requests                        pending, unexpired requests, newest first
GET  /v1/accounts/key-requests/{key_request_uid}
POST /v1/accounts/key-requests/{key_request_uid}/actions/cancel
```

The person submits the keys with `POST /v1/accounts` (registration) or
`PATCH /v1/accounts/{account_uid}` (rotation) plus `key_request_uid`. Before Alpaca is called or a
Secret is written, the API checks that the submitter is the request's person and that the request
is pending, unexpired (one hour), and matches the action, environment, and account; otherwise it
answers `404` (another person's or a missing request) or `409`. On success it completes the
request with the account UID. A `PATCH` with `key_request_uid` must carry `credentials`.

## MCP

`/mcp` serves the Agent's `alpaca` MCP application: stateless Streamable HTTP with JSON responses,
POST only, inside the same request identity as every route. `tools/list` returns the
`alpaca_connectors.agent_tools` catalog; `tools/call` validates the arguments against the tool's
schema and runs it in the API. Results and errors are JSON text, and errors never quote arguments.

These tools currently declare no MCP timeout metadata; TAU 2.0.11 therefore uses its default
60-second tool deadline unless the Agent runtime overrides it. Job submission is asynchronous,
but other synchronous mutations can outlive a caller's timeout. See
[application-tool timeouts](agent.md#application-tool-timeouts) before retrying a write.

Create and rotate record the acting user ([Authentication](#authentication)) as
`credentials_updated_by_user_uid`: the person an Agent's call works for, otherwise the verified
caller. Calls the API makes to Main Sequence, the Secret writes
included, run as the API's runtime principal. There is no trusted browser user UID. Governed
MetaTable operations declare read/write scope; the Resource Release is the deployed application
access boundary.

## Authentication

`create_app()` in `src/alpaca_connectors/api/app/main.py` builds the application and calls
`mainsequence.server.fastapi.install_request_identity(app)` once; `api/app/main.py` re-exports the
`app` it creates ([ADR 0014](adrs/0014_sdk_request_identity.md)). The platform's FastAPI launcher
(PodDeploymentOrchestrator since `3057764`) serves an application only when
`app.state.mainsequence_request_identity` declares that installation with the deployment's caller
mode and public routes. Without it every new revision fails at startup with
`Application request identity is not installed`, and the previous revision keeps serving.

Every route needs an authenticated caller, `/health`, `/openapi.json` and `/docs` included. The
workflow declares no `public_ingress`, so the deployment's `FASTAPI_PUBLIC_INGRESS` is empty and no
route admits an anonymous request. Hosted readiness is the launcher's own `/ms-health-deployment`,
which never reaches the application.

| Where | Caller proof | Rejected |
| --- | --- | --- |
| Hosted | The platform-signed caller assertion (`X-MainSequence-Caller-Assertion`) that the gateway forwards, verified against the release, Environment, issuer and key URL the backend sets on the deployment | 401 for a missing, invalid, expired or foreign assertion; 503 while the caller keys cannot be fetched |
| Local, without deployment markers | `Authorization: Bearer <token>`, checked against `MAINSEQUENCE_ENDPOINT/api/v1/users/me/` | 401 for a missing or rejected token; 503 when the backend fails or `MAINSEQUENCE_ENDPOINT` is unset |

`X-User-UID` alone never names a caller, and `X-User-ID` is rejected. Routes read the caller only
through `alpaca_connectors.api.app.identity`, which calls `User.get_logged_user()`; they never read
identity headers, `request.state` or the process's own SDK user. The caller decides:

- the credential author recorded when an account is created or its credentials rotated;
- the owner of an asset-registration operation, the only caller who can poll it;
- `request_user_uid` in `GET /v1/project-state/configuration`;
- the discovery `ETag`, which differs per caller.

The identity says who called, not what the caller may do: any authenticated caller may use every
route. The CORS middleware that `ALPACA_CONNECTORS_CORS_ORIGINS` configures wraps the identity, as
the launcher's own CORS does in a deployment. Preflights therefore need no caller, and an allowed
origin can read a 401.

Call a local API with the session of `mainsequence login`, giving the API and the CLI the same
development backend (for example `MAINSEQUENCE_ENDPOINT=http://127.0.0.1:8000`, the value the
repository `.env` holds):

```bash
curl -H "Authorization: Bearer $(.venv/bin/mainsequence auth token)" http://127.0.0.1:8321/health
```

The token is short-lived; request a new one when calls start answering 401. The static site's
standalone development mode sends no token, so it gets 401 from a local API until the site sends
one.

## Deployment

`.mainsequence/workflows/alpaca-connectors-api.yaml` declares the registered FastAPI resource with
automatic deployment, revision retention, and the platform-provided static-site origin policy. The
workflow is validated through the branch-owned backend validator.

```bash
uv run uvicorn alpaca_connectors.api.app.main:app --port 8321 --reload
```

Every request to the local API needs a Bearer token; see [Authentication](#authentication).
