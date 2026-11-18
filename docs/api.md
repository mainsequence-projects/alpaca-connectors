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
paths, such as `/v1/accounts/actions/remove-keys`. The resource adapter sends both requests to those
exact paths with the action's `method`; it does not resolve them against the collection path. The
offline API tests require every advertised path and method to match a registered route.

The provider-neutral HTTP contracts come from `msm.api.http` in `ms-markets>=1.0.14`:
collection pagination, resource discovery, bulk-action execution and preflight, sanitized generic
errors, and observable-operation wire models. Alpaca-specific resource declarations, option
parsing, provider-error classification, and business services remain in this repository. Asset
registration operation rows also remain in the project-owned MetaTable so polling survives API
process restarts; the shared in-memory operation repository is intentionally not used.

## Errors

An error answers `{"detail": {"code", "message", "retryable"}}` and never carries a dependency's
exception text. Besides the codes each section below names, every route can answer:

| Status | Code | When |
|---|---|---|
| `503` | `dependency_starting` | A Main Sequence release the work needs (the Markets API, the MetaTables API) is still starting. `retryable: true` and `Retry-After` (seconds): send the request again then ([ADR 0020](adrs/0020_dependencies_that_are_starting.md)). |
| `503` | `dependency_unavailable` | Anything unexpected; the API logs it. `retryable: true`. |
| `502` | `provider_failure` | Alpaca failed, or answered with data the connector cannot read (the log names the fields, never their values). |
| `409` | `action_conflict` | A conflict the connector finds itself, in its own words. |

A request waits for a starting release at most as long as its caller will
(`Mainsequence-Request-Timeout-Ms`, read by the SDK's request identity from mainsequence 9.0.39).
The API's startup starts the Markets API without waiting for it, so a caller that wakes the API
before its work finds the Markets API starting as well.

## Resources

| Resource | Main endpoints |
| --- | --- |
| Project State | `GET /health`, `GET /v1/project-state/capabilities`, `GET /v1/project-state/configuration` |
| Assets | `GET /v1/assets`, `GET /v1/assets/{asset_uid}`, synchronous registration plan/execute, and observable registration operations |
| Accounts | `GET/POST /v1/accounts`, `POST /v1/accounts/registration/preflight`, `GET/PATCH /v1/accounts/{account_uid}`, refresh, [remove keys and delete](#removing-keys-and-deleting-an-account), and bulk actions; the signed-in person's key requests under `/v1/accounts/key-requests` |
| Holdings | list/get and capture under `/v1/accounts/{account_uid}/holdings`; `/latest` lists only the newest immutable snapshot; the [account sync](#account-sync) at `POST /v1/accounts/{account_uid}/actions/sync` |
| Execution | submit (with preflight), sync and cancel order batches, and list recorded orders, under `/v1/accounts/{account_uid}/orders` |
| Market Data | configuration CRUD and resolve/update actions under `/v1/market-data/bar-configurations`; dataset list/get and bounded observations under `/v1/market-data/datasets` |
| Portfolios | an account's signals under `/v1/signal-definitions`, with their weights at `/{uid}/weights`; the account's portfolios that follow them: create at `POST /v1/portfolio-configurations`, list/get/`PATCH`/`DELETE`, and run and JobRun-history actions on the portfolio Job under `/v1/portfolio-configurations`; ADR 0024's routes `POST /v1/portfolio-configurations/provided-weights` and `/{uid}/weights`, supported with their former rules (Hunch uses them); reusable calendar-event policies under `/v1/portfolio-rebalance-configurations` |
| Operations | poll an accepted Alpaca bars or portfolio JobRun under `GET /v1/operations/job-runs/{job_run_uid}`; read an account sync again at `GET /v1/operations/{operation_uid}` |

Market-data writes are configuration-oriented. A bar configuration has exactly one parent
([ADR 0028](adrs/0028_every_portfolio_belongs_to_one_account.md)): a feed of an account, created
here by the account's owner with the `assets` or `account_holdings` source and deleted with the
account, or a portfolio's own configuration, with the `portfolios` source, created and deleted with
the portfolio. Review one with the `resolve` action and run it with the `update` action. Every
configuration writes the one shared price table of its bar profile, whose rows name no account. Neither request accepts a dataset UID. The response reports the
derived output MetaTable only as diagnostics.

A bar configuration's `scheduled` field (default `true`) includes it in the daily bars update at
00:00 UTC ([ADR 0021](adrs/0021_daily_bars_dispatcher.md)). An unscheduled configuration still
runs through the `update` action while it is `enabled`; a disabled one runs neither way. The list
filters on `scheduled` as on `enabled`.

The bar-configuration `update` action is asynchronous. It performs a read-only configuration
preflight, submits the branch-owned `Alpaca Bars Update` Job with exactly
`--configuration-uid <UUID>`, and returns `202 Accepted` with a JobRun status URL. The HTTP process
does not contact Alpaca or write bars. Poll responses are never cached and expose only sanitized
failure information plus the platform log URL.

## Order Execution

Execution is for applications ([ADR 0016](adrs/0016_order_execution_for_authorised_applications.md));
the Alpaca Agent has no tool over it. An application in another repository declares `user` on this
release in its own workflow (`access.branches: [{repository: <this repository>, policy: user}]`).
Whose account it may trade is that application's decision, as it is for the holdings capture.

```text
POST /v1/accounts/{account_uid}/orders/actions/submit/preflight
POST /v1/accounts/{account_uid}/orders/actions/submit
POST /v1/accounts/{account_uid}/orders/actions/sync      {"batch_id": "..."}
POST /v1/accounts/{account_uid}/orders/actions/cancel    {"batch_id": "..."}
GET  /v1/accounts/{account_uid}/orders?batch_id=...
```

A submission names the batch and its orders:

```json
{
  "batch_id": "HUNCH_BATCH__5d1c",
  "queue_when_closed": false,
  "orders": [
    {
      "client_order_id": "5d1c:0",
      "asset_identifier": "ALPACA::b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
      "symbol": "AAPL",
      "side": "buy",
      "quantity": 1.5,
      "order_type": "market",
      "time_in_force": "day"
    }
  ]
}
```

`asset_identifier` is the registered asset's canonical identifier; the order is placed on the live
Alpaca asset behind it, under Alpaca's current symbol. Only market day orders are accepted (`422`
otherwise). Every answer has the same shape: `batch_id`, `account_uid`, the batch `status`,
`accepted`, an optional `message`, `warnings`, the `holdings_set_uid` published after the latest
fills with its `holdings_time`, and `orders`, one per request leg, with `client_order_id`, `broker_order_id`, `status`
(`submitted`, `partially_filled`, `filled`, `cancelled`, `rejected`, `expired`, `failed`),
`venue_status` (Alpaca's own), `filled_quantity`, `average_fill_price`, `submitted_at`,
`updated_at` and `message`.

- **Preflight and submit** run the pre-trade checks: the registration and the Alpaca account are
  active and tradable, every asset is a registered Alpaca asset that is tradable (and fractionable
  when the quantity is a fraction), the market is open (or `queue_when_closed` is set, and Alpaca
  queues the day orders for the next session), and the buys' latest-trade notional net of the
  sells fits Alpaca's buying power. A failed check is `409` with
  `{"code", "message", "retryable": false, "checks": [{"code", "message", "client_order_id"}]}`,
  `code` being the failed check's code or `pre_trade_checks_failed` when several failed. The
  preflight answer is the bulk-action preflight contract with the same `checks`.
- **Submit** is idempotent on `client_order_id`: an order Alpaca already holds is read back, not
  placed again. Sells go first; with both sells and buys in one batch, the buys wait up to ten
  seconds for the sells to settle. An order Alpaca refuses at submission comes back `rejected`
  with Alpaca's reason while the rest of the batch proceeds; `accepted` stays true because the
  batch was processed.
- **Sync** re-reads every order of the batch from Alpaca, updates the ledger and publishes a
  holdings snapshot when it records new fills. **Cancel** asks Alpaca to cancel the open orders
  and re-reads them; Alpaca cancels asynchronously, so an order can still read `submitted` until
  the next sync.
- **The ledger** is ms-markets': one `OrderManager` row per batch (`unique_identifier` is the
  `batch_id`), one `OrdersTS` row per order and one `TradesTS` row per terminal order with a fill.
  `GET .../orders` reads the batch rows only and never calls Alpaca.
- `GET /v1/project-state/capabilities` lists every capability as a feature too:
  `{"id": "execution", "status": "available"}` says this release executes.

## Project State And The Site's Agent

The Alpaca site reads `GET /v1/project-state/configuration` on page load. Besides the migrated
market-data profiles and the account and dataset counts, it names the Agent that the site's
conversation opens:

| Field | Value |
| --- | --- |
| `agent_uid` | This branch's deployed Alpaca Agent, or `null` when none can be named exactly |
| `organization_environment_uid` | The branch's Organization Environment; when `agent_uid` is set, that Agent's own |
| `organization_environment_name` | That Environment's name |

The API finds the Agent when the request arrives (`alpaca_connectors.api.app.site_agent`), as the CRM
API does. It lists Agents by the name in `.agents/agent_card.json`, then keeps the one that is in
the Environment and on the CodeRepositoryBranch the SDK context resolves, and that has a deployed
runtime release. Nothing comes from the request. The workflow gives the API `user` on the Agent
(`access.resources`), which is what lets the hosted API see it.

If the Agent is missing, ambiguous or not deployed, or the lookup fails, the answer carries
`agent_uid: null` instead of failing the configuration. The site then shows its conversation as
unavailable, and its other pages keep working.

The site takes both UIDs from this one answer, never from its build or from the person's Command
Center Environment. A re-created Agent has a new UID, and the API's next answer names it without a
site rebuild. The `alpaca_project_state` MCP tool returns the same configuration.

## The Portfolio Job

Portfolio configurations run on the `alpaca-portfolio` Job ("Alpaca Portfolio") declared in
`.mainsequence/workflows/alpaca-connectors-api.yaml`, and its dispatcher,
`alpaca-portfolio-dispatcher`, starts their scheduled runs at 02:00 UTC
([ADR 0013](adrs/0013_workflow_declared_configuration_jobs.md),
[Jobs](operations/jobs.md#the-portfolio-job)).

- **Requests.** Create and update requests accept `scheduled` (default true). It decides whether
  the daily scheduled run includes the configuration. A request that still sends schedule or
  compute settings (a nested `job`) gets 422 naming the workflow file, because they belong to the
  declaration.
- **Responses.** They report `scheduled` and the Job as `job_uid` and `job` (its schedule, compute
  and image). Both are null until a push creates the Job on the branch.
- **Runs.** A run returns 202 with the JobRun UID and a `status_url`. It starts the Job with
  `--configuration-uid <uid>` and is refused while that configuration's newest run is pending or
  running. Run lists and `/v1/operations/job-runs/{job_run_uid}` find a configuration's runs by that
  argument and return it as `command_args`.

## Portfolios

An application builds a portfolio from weights it chooses
([ADR 0024](adrs/0024_portfolios_from_provided_weights.md)), and that is the connector's only
kind of portfolio ([ADR 0027](adrs/0027_one_portfolio_path.md)). Each portfolio belongs to one
account and follows one of that account's signal definitions
([ADR 0028](adrs/0028_every_portfolio_belongs_to_one_account.md)). A definition's signal is
ms-markets' `ApplicationWeights`, configured with this connector's namespace and a key made of the
account UID and the application's key, so two accounts never share a signal. The supported
rebalance strategy is `CalendarEventSignal`, backed by persisted market-session events; valuation
uses daily persistent `InterpolatedPrices`, forward-fill interpolation, and a bounded staleness
policy. The [Portfolios page](portfolios/portfolios.md) describes the whole flow.

```text
GET|POST         /v1/signal-definitions
GET|PATCH|DELETE /v1/signal-definitions/{definition_uid}
POST|GET         /v1/signal-definitions/{definition_uid}/weights

POST             /v1/portfolio-configurations
GET              /v1/portfolio-configurations
GET|PATCH|DELETE /v1/portfolio-configurations/{configuration_uid}
POST             /v1/portfolio-configurations/{configuration_uid}/actions/run
GET              /v1/portfolio-configurations/{configuration_uid}/runs

POST             /v1/portfolio-configurations/provided-weights           (ADR 0024, until moved)
POST|GET         /v1/portfolio-configurations/{configuration_uid}/weights (ADR 0024, until moved)

GET|POST         /v1/portfolio-rebalance-configurations
GET|PATCH|DELETE /v1/portfolio-rebalance-configurations/{configuration_uid}
```

- **A signal definition** takes `account_uid`, the application's `key` (unique within the
  account), `name` and `description?`. Only the account's owner creates it, gives it weights,
  renames it or deletes it; nobody does on an account without an owner (`409
  account_without_owner`). It answers its `signal_uid` and how many portfolios follow it, never its
  stored key. A key already used in the account answers `409 signal_key_exists`; a key whose
  signal a retired portfolio still follows answers `409 signal_key_taken`. Deleting it is refused
  while a portfolio follows it (`409 signal_followed`); otherwise its observations, signal-asset
  rows and Markets `SignalMetadata` are deleted with it, unless a retired `Portfolio` still follows
  the signal, in which case they stay with that `Portfolio`.
- **Weights** go to the definition, `POST /v1/signal-definitions/{uid}/weights`, and every
  portfolio that follows it uses them.
- **Create** (`POST /v1/portfolio-configurations`) takes `name`, `description?`, `account_uid`,
  `signal_definition_uid` (a definition of the same account), `rebalance_configuration_uid`, the
  bar profile of the portfolio's own bars configuration under `bars` (`frequency_id`, `feed`,
  `adjustment`, `enabled?`, `scheduled?`, `name?`), the valuation and fee settings and
  `scheduled?`. Only the account's owner creates it. It answers the configuration, the `Portfolio`
  (`portfolio_uid`, `portfolio_unique_identifier` `ALPACA_PORTFOLIO__<uid hex>`) and its bars
  configuration. A refused create leaves no row.
- **ADR 0024's create** (`POST /v1/portfolio-configurations/provided-weights`) is supported, and
  Hunch uses it: it takes either `bars_configuration_uid` (a feed) or `bars_configuration`
  (`name`, `account_uid`, `frequency_id`, `feed`, `adjustment`, `scheduled?`), and the
  `observations`. It keeps the rules it had before ADR 0028: the caller owns the portfolio, a named
  feed is not checked, and an inline one needs an account the caller may configure (another
  owner's answers `409 account_owned_by_another`; one without an owner stays open, ADR 0022). The
  portfolio belongs to that account, gets its own bars configuration with that profile, and a
  signal definition whose key is its configuration UID, as its signal's key was. Its
  `/{uid}/weights` routes publish to the signal the portfolio follows.
- **An observation** is `{observed_at, weights: {<ALPACA::uuid>: <weight>}}`. `observed_at` needs a
  timezone. Each observation is the whole set: an asset the previous one held and this one leaves
  out is written at `0`. Weights are stored as given, negatives included. Several observations in
  one call start or extend a history; an observation at or before the last stored one is refused
  (`409 observation_not_after_last`), because published observations are never restated.
- **Assets** must be registered Alpaca assets: `422 unknown_asset` or `422 asset_not_alpaca`
  otherwise. Other refusals are `422 invalid_observation` and `422 empty_observations`. No error
  names an asset, a weight or an owner.
- **The owner.** The acting owner at creation owns the portfolio: the account's owner on
  `POST /v1/portfolio-configurations`, the caller on ADR 0024's create. Only that owner gives it
  weights, changes, pauses or deletes it; anyone else gets `403
  portfolio_owned_by_another`. Reads and runs stay open. Responses never carry the owner, but carry
  `account_uid`, `signal_definition_uid` and `adopted`.
- **Not adopted yet.** A portfolio created before ADR 0028 that revision 0029 could not give an
  account (its owner does not own its bars' account) answers `adopted: false` and works as before:
  it takes weights on its own signal, keyed by its configuration UID, runs on the feed it names,
  and that feed keeps pricing it. Nothing ends it.
- **Deleting ends it.** `DELETE` retires the ms-markets `Portfolio` (ms-markets ADR 0049), then
  deletes the configuration with its own bars configuration, and answers `portfolio: {uid,
  unique_identifier, state: "retired", retired_at}`. While an account's current target names the
  portfolio it answers `409 portfolio_in_account_target` and changes nothing. The `Portfolio`, its
  series, its signal and account history stay, recorded under the account, whose delete deletes
  them; a retired portfolio is no longer offered as a target and is no longer computed.
- **No schedule or compute is stored.** The configuration's only operational field is
  `scheduled`. `PATCH {"scheduled": false}` pauses a portfolio without validating its references.
  A run also needs an allowed preflight: the portfolio must be adopted, its own bars configuration
  must be enabled, its rebalance configuration must exist, and its signal must have at least one
  observation.

There is no Environment request field. A run selects its configuration with
`--configuration-uid <uid>` and updates persistent interpolation, calendar events, rebalance state,
executed weights, and valuation without rerunning the raw Bars producer. Rebalance rows use actual
configured market-event timestamps and select the latest observation at or before each event.

The configuration detail `GET` accepts `observation_limit` from 1 through 5,000 (default 2,500). It
returns the resolved Bars, Rebalance, Job, and canonical portfolio/calendar summaries (read through
the Markets API), a `provided_weights` block (the signal UID, its first and last observation times
and its asset count), plus the requested latest values from this application's `PortfoliosTS`
instance (`alpaca_connectors__portfoliosts__main`). The history query is scoped by the canonical
Portfolio unique identifier and runs only when a consumer opens that configuration; API consumers
do not need to interpret or display related resource UIDs.

The same response includes window-scoped performance statistics calculated from those canonical
daily values with `empyrical-reloaded`: total and annualized return, annualized volatility, Sharpe,
Sortino, maximum drawdown, Calmar, best/worst daily return, and the positive-period ratio. The
response records the exact observation count, date window, 252-period annualization factor, and 0%
risk-free-rate assumption. Alpha and beta are not reported because this workflow has no configured
benchmark. If the requested window is shorter than the stored history, the response marks it as
truncated rather than presenting the statistics as since-inception results.

A `portfolios` bars configuration fetches every asset ever published by the live portfolios that
name it: a deleted portfolio or one whose `Portfolio` is retired drops out, a paused one still
counts. The list is derived at every resolution from the portfolio configurations and the
signal-asset table (`alpaca_connectors__signal_asset`), never from the signal weights series; the
`resolve` action shows it. Bars run at 00:00 UTC and portfolios at 02:00 UTC, so weights given
during a day get their bars before the next scheduled run. Run the bars update first when running
a portfolio at once after adding an asset.

## Account Sync

Every venue connector answers an application's request for an account's holdings the same way
([issue #14](https://github.com/mainsequence-projects/alpaca-connectors/issues/14)): the connector
reads the venue with the account's stored keys and publishes one `AccountHoldingsSet` into
ms-markets, and the application reads the snapshot from ms-markets, not from this answer.

When the account has a target in ms-markets, the sync also sizes it and allocates the snapshot to
the account's funds ([ADR 0023](adrs/0023_account_sync_sizes_and_allocates_funds.md)):

| Step | What it does |
| --- | --- |
| `capture_holdings` | Reads Alpaca and publishes the holdings snapshot at time `T` |
| `size_targets` | Reads the account's latest target and turns each exposure into units: a share of NAV is taken from Alpaca's reported equity, split by the portfolio's latest weights at or before `T`, and divided by Alpaca's latest trade. A whole share is rounded half up unless the asset is fractionable |
| `allocate_funds` | Plans the snapshot against those units, then allocates with the plan's digest. ms-markets records each owner's target, what it was assigned and the delta |

The connector writes no target and trades no delta. An account with no target, and a capture
that published no snapshot, end after `capture_holdings` with the two other steps `skipped`.

```text
POST /v1/accounts/{account_uid}/actions/sync
GET  /v1/operations/{operation_uid}
```

`account_uid` is the ms-markets `Account` uid; the POST takes no body. It runs the three steps
inside the request and answers `200` with a terminal
`ObservableOperation[AccountSyncRequest, AccountSyncResult]`, `action: "account_sync"`:

```json
{
  "operation_uid": "0f6d2c1e-4b8a-4c1f-9a51-3a7e2b9d6c40",
  "action": "account_sync",
  "status": "succeeded",
  "current_step": null,
  "steps": [
    {"key": "capture_holdings", "label": "Read the Alpaca account and publish its holdings", "status": "succeeded", "...": "..."},
    {"key": "size_targets", "label": "Size the account's target into units", "status": "succeeded", "...": "..."},
    {"key": "allocate_funds", "label": "Allocate the holdings to the account's funds", "status": "succeeded", "...": "..."}
  ],
  "request": {"account_uid": "..."},
  "result": {
    "account_uid": "...",
    "holdings_set_uid": "...",
    "time_index": "2026-11-09T15:30:00Z",
    "holdings_rows": 3,
    "position_set_uid": "...",
    "allocation_applied": true,
    "lines": [
      {"owner": "fund", "target_portfolio_uid": "...", "asset_uid": "...", "asset_identifier": "...", "target": 1200.0, "assigned": 1000.0, "delta": 200.0}
    ],
    "extra": {"unresolved_symbols": []}
  },
  "error": null
}
```

- `position_set_uid` is the target the snapshot was allocated against, or null when the account
  has none. `allocation_applied` is false when the same allocation was already written or the
  steps were skipped. `lines` repeats what ms-markets recorded, with `delta = target - assigned`;
  read them again with `GET /api/v1/account/{account_uid}/virtual-fund-allocations/` on the
  Markets API.
- A step that fails after the capture leaves the snapshot published. The operation is `failed`
  with `current_step` naming the step, and the next sync captures again. Its codes are
  `account_equity_missing`, `portfolio_weights_missing` (a portfolio of the target has no
  weights in this connector at `T`), `asset_unresolved`, `asset_price_missing`,
  `invalid_account_target`, and the Markets refusals `not_found`, `target_after_snapshot`,
  `invalid_sized_targets`, `plan_changed`, `allocation_exists`, `policy_version_mismatch` and
  `portfolio_retired`. `target_after_snapshot`, `plan_changed` and `policy_version_mismatch`
  are `retryable`. No message names an asset, a portfolio or an amount.
- A capture that fails is still `200`, with `status: "failed"`, `result: null` and
  `error: {code, message, retryable}`: the code and message the account routes would answer
  (`alpaca_credentials_rejected`, `credential_key_unavailable`, `account_credentials_missing`,
  `action_conflict` for a held asset that cannot be registered or that Markets stores with another
  asset type, `provider_failure`,
  `dependency_starting` with `retryable: true` while a release it needs starts, and
  `dependency_unavailable` for anything unexpected, with no internals).
- A refused request is an HTTP error and runs nothing: `404` for an unknown account, or for an
  account that is not the acting owner's under [ADR 0019](#account-owners), as
  `GET /v1/accounts/{account_uid}` answers it.
- Each sync is one row of the `alpaca_connectors__account_sync_operation` MetaTable, owned by the
  acting owner. `GET /v1/operations/{operation_uid}` answers that owner the same operation, from
  any API process, with `Cache-Control: no-store`; any other caller, and an unknown UID, get `404`.
- No key value appears in an operation, its row, a log line or an error. The API logs each sync
  with the account UID, the owner pair and, on failure, the error code.
- `AccountSyncRequest` and `AccountSyncResult` are defined here until ms-markets ships them in
  `msm.api.http`
  ([MainSequenceMarkets#44](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/44)),
  with the same fields.
- `POST /v1/accounts/{account_uid}/holdings/actions/capture` and the bulk
  `POST /v1/accounts/actions/capture-holdings` stay, running the same capture, until HunchAPI has
  moved to the sync.

## Observable Asset Registration

Browser clients should start registration work through:

```text
POST /v1/assets/registration/operations
GET  /v1/assets/registration/operations/{operation_uid}
```

The start request contains `action` (`plan` or `execute`) and the validated registration request.
That request requires `account_uid`; the backend opens the selected registered account's stored
keys for Alpaca access and never accepts credential values.
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
by `source` ([ADR 0017](adrs/0017_encrypted_account_credentials.md)):

- `{"source": "managed", "api_key": "...", "secret_key": "..."}`: the API validates the values
  against Alpaca and stores them encrypted on the account row (`credential_source: managed`).
  Both fields are `writeOnly` `password` strings in OpenAPI and are never returned. The static
  site uses only this shape.
- `{"source": "external", "api_key_secret_name": "...", "secret_key_secret_name": "..."}`:
  existing Main Sequence Secrets the API reads once, as its workload, to import the keys
  (`credential_source: imported`). The names are not kept and the Secrets are never changed.

`PATCH` without `credentials` changes only the name or active state and never reads credentials.
Rotation must authenticate as the registered Alpaca account and replaces the stored keys in one
write. Preflight reports the identity, holdings and `credential_source` and writes nothing.
`POST /v1/accounts` writes the Account, its held assets and its initial holdings with one Markets
`registerAccount` call (ms-markets 3.4.0 or later), then the account's row, and answers from what
it wrote ([registration](account/registration.md#holdings)). Sending the same registration again,
for example after a client timeout, succeeds; a held asset that Markets stores with another asset
type answers `409 action_conflict` and writes nothing.
Responses expose `credential_source`, `credentials_stored`, `credentials_removed` and
`credentials_updated_at`/`_by_user_uid`; never values, Secret names or the key fingerprint.
`credentials_removed` is true once the owner removed the keys; `credentials_stored` is false
then, and for a registration made before revision `0018` that the credential migration has not
copied yet. Removing the keys and deleting the account report `deleted_secrets` and `warnings`,
empty unless the registration predated revision `0018`
([below](#removing-keys-and-deleting-an-account)).

Alpaca rejecting the keys returns `400 alpaca_credentials_rejected` without provider text. A
registration whose keys have not been migrated yet returns `409 account_credentials_missing`; an
API runtime that cannot read the `ALPACA_CONNECTORS_DATA_KEY` Secret returns
`503 credential_key_unavailable`, naming the Secret and nothing else. Every `422` response lists
only `loc`, `msg`, and `type`: FastAPI's default body echoes the rejected `input`, which could be
a credential.

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

## Account Owners

An account belongs to a person pair, `owner: {gateway_uid, user_uid}`
([ADR 0019](adrs/0019_account_owners_vouched_for_by_trusted_applications.md)). A Main Sequence user
is `(uid, uid)`. An external person, one of a gateway application's people who is not a Main
Sequence user, is `(gateway workload User UID, the gateway's opaque person id)`; the two values
always differ.

A vouching caller names the person in the request's W3C Baggage (tdag-django ADR-0057) with
`mainsequence.<segment>.external_user_gateway` and `mainsequence.<segment>.external_user_uid`. The
API believes it only from a caller listed in `ALPACA_CONNECTORS_VOUCHING_CALLERS`, a JSON list of
`{caller_uid, segment, gateway_uids}` set in the API's workflow file (empty today):

| Call | Acting owner |
| --- | --- |
| The Agent working for a person (requester-bound) | `(requester, requester)`; caller context ignored |
| A listed caller naming a person through an allowed gateway | `(gateway, person id)` |
| A listed caller naming a person through another gateway | `403 caller_context_untrusted_gateway` |
| A listed caller with one key, an invalid value or equal values | `400 caller_context_invalid` |
| A listed caller with no person, or any other caller | `(caller, caller)` |

- The owner is set by the first registration and never changed by a rotation. Registering an
  Alpaca account that another owner holds answers `409 account_owned_by_another`, before any
  write, and does not name the owner. A registration without an owner claims it.
- A call for an external person reaches only that person's accounts: every route that names an
  account, in its path, body or options, answers `404` for anyone else's, and `GET /v1/accounts`
  lists only theirs. Account key requests are refused for them (`400`). Other calls are unchanged.
- Only an account's owner creates, changes or deletes its signal definitions and creates portfolios
  on it through `POST /v1/portfolio-configurations`
  ([ADR 0028](adrs/0028_every_portfolio_belongs_to_one_account.md)): anyone else gets `409
  account_owned_by_another`, and nobody does on an account without an owner (`409
  account_without_owner`) until it takes one at its next key registration. Bars feeds and ADR
  0024's routes keep the rules they had before ADR 0028 (ADR 0022): a feed may not run with another
  owner's account (`409 account_owned_by_another`), an account without an owner stays open, and
  deleting a feed checks no owner. A portfolio's own bars configuration is changed by the
  portfolio's owner. An external person gets `404` for anyone else's account.
- An external person's registration of an account without an owner answers `409 account_in_use`
  while enabled bar configurations run with its keys. It names none of them.
- `GET /v1/accounts` filters by `owner_gateway_uid` and `owner_user_uid`, given together.
- A malformed `ALPACA_CONNECTORS_VOUCHING_CALLERS` stops the API from starting.

### Removing keys and deleting an account

Two separate actions, both for the account's owner only
([ADR 0028](adrs/0028_every_portfolio_belongs_to_one_account.md), decision 8):

- **Remove keys**, `POST /v1/accounts/{account_uid}/actions/remove-keys` or the bulk
  `POST /v1/accounts/actions/remove-keys` (with its preflight), clears the stored keys. The owner,
  the account, its history, portfolios and configurations stay; nothing is disabled, and runs fail
  with the ordinary error until the owner registers keys again.
- **Delete**, `DELETE /v1/accounts/{account_uid}?confirm=DELETE%20<account_uid>`, deletes the
  account and everything that belongs to it: its portfolios (retired, then deleted unless another
  account's history names them), with those not yet adopted that its `portfolios` feeds price,
  their bars configurations, its bars feeds, its signal
  definitions and their signals, its orders and trades, and its whole Markets history. A request
  without the exact confirmation changes nothing. It is refused with `409 account_delete_refused`,
  before anything changes, while ms-markets' preflight reports a blocker or another account's
  current target holds one of its portfolios; the refusal names no other account.
  `POST /v1/accounts/{account_uid}/actions/delete/preflight` reports the same without changing
  anything, with the counts of what would go. A repeated request finishes what an interrupted one
  left. The answer lists the deleted and kept portfolio and signal UIDs and, for a registration
  made before revision `0018`, the Secrets this application created that went with it.

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
caller. The first registration also records the account's owner, reported as `owner` on every
account response ([Account Owners](#account-owners)). Calls the API makes to Main Sequence and MetaTables, the encrypted key writes
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
development backend (for example `MAINSEQUENCE_ENDPOINT=http://local-backend:8000`, the value the
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
