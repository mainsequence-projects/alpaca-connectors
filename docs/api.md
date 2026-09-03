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
| Holdings | list/get and capture under `/v1/accounts/{account_uid}/holdings` |
| Universe Sources | CRUD, source preview, and source-backed bulk sync under `/v1/universe-sources` |
| Universes | Create/list/get/update managed `HOLDINGS__*` categories plus run, activate, deactivate, and confirmed delete actions under `/v1/universes` |
| Market Data | configuration CRUD and resolve/update actions under `/v1/market-data/bar-configurations`; dataset list/get and bounded observations under `/v1/market-data/datasets` |
| Operations | poll an accepted Alpaca bars JobRun under `GET /v1/operations/job-runs/{job_run_uid}` |

Market-data writes are configuration-oriented. Create one stored definition with exactly one
source (`assets`, `universe`, or `account_holdings`), review it with the `resolve` action, and run it
with the `update` action. Neither request accepts a dataset UID. The response reports the derived
output MetaTable only as diagnostics.

The bar-configuration `update` action is asynchronous. It performs a read-only configuration
preflight, submits the branch-owned `Alpaca Bars Update` Job with exactly
`--configuration-uid <UUID>`, and returns `202 Accepted` with a JobRun status URL. The HTTP process
does not contact Alpaca or write bars. Poll responses are never cached and expose only sanitized
failure information plus the platform log URL.

`POST /v1/universes` requires a name, ETF ticker, and explicit holdings source URL. It stores the
source and creates an empty category with zero memberships; it does not extract holdings. Run is a
separate discovery action on the created universe. Its preflight performs extraction and strict
asset resolution, and execution synchronizes memberships only when that plan has no blockers. The
removed ticker/provider inference routes are not retained as aliases.

## Observable Asset Registration

Browser clients should start registration work through:

```text
POST /v1/assets/registration/operations
GET  /v1/assets/registration/operations/{operation_uid}
```

The start request contains `action` (`plan` or `execute`) and the validated registration request.
The API returns `202 Accepted` with a stable operation UID and an ordered step list. Clients poll the
GET route using the returned `poll_after_ms` value. Poll responses use `Cache-Control: no-store`.

Operation states are `queued`, `running`, `succeeded`, and `failed`. Step states are `pending`,
`running`, `succeeded`, `failed`, and `skipped`. Every transition records timestamps and a user-safe
message. A successful operation includes the normal plan or execution result. A failed operation
retains only the sanitized public error contract; provider exception internals are not recorded.
Alpaca catalog loading and OpenFIGI identity resolution are separate steps. The error code and
message identify credential lookup, Alpaca, OpenFIGI, or Main Sequence as the failing dependency;
an upstream failure explicitly reports that OpenFIGI was not called.

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
