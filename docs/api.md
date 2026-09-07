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

## Resources

| Resource | Main endpoints |
| --- | --- |
| Project State | `GET /health`, `GET /v1/project-state/capabilities`, `GET /v1/project-state/configuration` |
| Assets | `GET /v1/assets`, `GET /v1/assets/{asset_uid}`, synchronous registration plan/execute, and observable registration operations |
| Accounts | `GET/POST /v1/accounts`, `GET /v1/accounts/secret-references`, `GET/PATCH/DELETE /v1/accounts/{account_uid}`, refresh and bulk actions |
| Holdings | list/get and capture under `/v1/accounts/{account_uid}/holdings`; `/latest` lists only the newest immutable snapshot |
| Universe Sources | Explicit extraction-configuration CRUD and read-only source preview under `/v1/universe-sources` |
| Universes | Create/list/get/update registered `AssetUniverse` rows; list linked category Assets at `GET /v1/universes/{universe_uid}/assets`; run, activate, deactivate, and confirmed delete actions under `/v1/universes` |
| Market Data | configuration CRUD and resolve/update actions under `/v1/market-data/bar-configurations`; dataset list/get and bounded observations under `/v1/market-data/datasets` |
| ETF Signals | dedicated Job configuration CRUD under `/v1/signal-jobs`; run, pause, resume, reconcile, and JobRun-history actions |
| ETF Portfolios | calculation-only configuration CRUD under `/v1/portfolio-configurations`; reusable ImmediateSignal policies under `/v1/portfolio-rebalance-configurations`; run and JobRun-history actions |
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

## Scheduled ETF Signals

`POST /v1/signal-jobs` creates one durable signal configuration and one dedicated Job. The request
selects the current Organization Environment, one Universe, one registered Alpaca account, one
schedule, and compute settings. `universe_uid` defines signal identity. `account_uid` is runtime
configuration and never becomes part of the final signal UID or `SignalWeightsStorage`.

The Job is created without a schedule, linked to the stored configuration, and only then scheduled.
Manual and scheduled runs pass no business arguments. The launcher resolves the configuration via
`JOB_RUN_UID -> job_uid`, then performs one Universe extraction/materialization/signal update.

Lifecycle routes are:

```text
POST /v1/signal-jobs/{configuration_uid}/actions/run
POST /v1/signal-jobs/{configuration_uid}/actions/pause
POST /v1/signal-jobs/{configuration_uid}/actions/resume
POST /v1/signal-jobs/{configuration_uid}/actions/reconcile
GET  /v1/signal-jobs/{configuration_uid}/runs
GET  /v1/signal-jobs/{configuration_uid}/observations?limit=100
```

Deleting the configuration removes its dedicated Job and desired-state row while retaining prior
signal observations.

The observations endpoint resolves the signal UID from the stored configuration, selects the
latest distinct observation timestamps from canonical ms-markets `SignalWeightsStorage`, and
returns all constituent rows for those timestamps as a compact aligned payload. It never accepts an
arbitrary signal UID and performs one set-based backend query rather than one request per Asset.
The limit is between 1 and 100 and defaults to 100. Timestamps are returned chronologically and each
Asset carries one aligned weight per timestamp; the frontend projects that payload with timestamps
as rows and Assets as columns. Because signal updates publish complete frames, an absent Asset row
is represented as zero while an explicit stored null remains null.

## Scheduled ETF Portfolios

`POST /v1/portfolio-configurations` creates one durable calculation definition and one dedicated
Main Sequence Job. The calculation definition references an existing ETF Signal Configuration,
Bars Configuration, and Rebalance Configuration. Phase 1 accepts only `ImmediateSignal`, daily
persistent `InterpolatedPrices`, and forward-fill interpolation.

Job settings are nested under `job` in create and update requests. They are written only to the
Main Sequence Job; the Portfolio Configuration MetaTable does not duplicate schedule, compute,
image, deployment, Environment, or JobRun state. Responses compose the calculation row with a live
Job projection.

```text
GET|POST /v1/portfolio-configurations
GET|PATCH|DELETE /v1/portfolio-configurations/{configuration_uid}
POST /v1/portfolio-configurations/{configuration_uid}/actions/run
GET  /v1/portfolio-configurations/{configuration_uid}/runs

GET|POST /v1/portfolio-rebalance-configurations
GET|PATCH|DELETE /v1/portfolio-rebalance-configurations/{configuration_uid}
```

There is no Environment request field. Manual and scheduled JobRuns contain no business
arguments. The launcher resolves `JOB_RUN_UID -> Job.uid -> Portfolio Configuration.job_uid`,
updates persistent interpolation, and calculates the portfolio without rerunning the Signal or raw
Bars producers. Signal observation timestamps are observation times, so the ImmediateSignal result
is an analytical reconstruction rather than guaranteed point-in-time ETF replication.

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
`alpaca_connectors__asset_registration_operation` MetaTable and scoped to the request-bound user UID
when the platform supplies one. The existing synchronous plan and execute routes remain available
for non-interactive callers.

The record is durable, but execution currently runs as a FastAPI background task rather than a
platform Job. A process termination can therefore leave an accepted operation in `queued` or
`running`; clients must use a bounded polling policy. Restart recovery is intentionally not claimed.

## Credentials And Identity

Account create/update requests accept only `api_key_secret_name` and
`secret_key_secret_name`. Raw Alpaca keys and secure-configuration value payloads are rejected by
the request schemas and never returned.

`GET /v1/accounts/secret-references` returns only Main Sequence Secret names visible to the API
runtime. It exists solely to populate account credential-reference pickers and never serializes
Secret values.

In a deployed release, Main Sequence injects the authenticated human as `request.state.user` and
`request.state.user_uid`. There is no `request.state.user_id`, local SDK identity middleware, or
trusted browser user UID. Governed MetaTable operations declare read/write scope; the Resource
Release is the deployed application access boundary.

## Deployment

`.mainsequence/workflows/alpaca-connectors-api.yaml` declares the registered FastAPI resource with
automatic deployment, revision retention, and the platform-provided static-site origin policy. The
workflow is validated through the branch-owned backend validator.

```bash
uv run uvicorn api.app.main:app --reload
```
