# ADR: Order Execution For Authorised Applications

## Status

Accepted on 2026-10-26. Supersedes the "no trading, ordering" rule that `AGENTS.md`, the
[capability model](../capabilities/index.md) and [ADR 0015](0015_agent_tools_on_the_api_mcp_and_site_key_entry.md)
stated for this repository: the API now places orders. The Alpaca Agent still does not; that part
of ADR 0015 stands.

Amended on 2026-11-16 by [ADR 0024](0024_portfolios_from_provided_weights.md): a provided-weights
portfolio has an owner, and only it changes the portfolio.

Amended on 2026-11-23: preflight and submit no longer run the same checks (decision 3). The
preflight reports every check before anything is placed. Submit runs only those that protect what
it places and records, which Alpaca cannot enforce, and leaves the rest to Alpaca.

## Context

Until now the connector was read-only toward Alpaca: it registered accounts, captured holdings and
resolved assets. Every document and test asserted "no trading", and the Alpaca Agent's tool
catalog, prompt and skills promised the same.

The Hunch backend (`hunchapi`) builds portfolios from people's theses and funds them into the
person's own Alpaca account. Its decision (hunchapi ADR 0003, plan §3.3 and §6.1) is that Hunch owns
the execution logic (targets, proposals, approvals, autopilot limits, state) and delegates the exact
orders to the connector of the account's venue, which owns the credentials. Hunch's Alpaca client
already targets this connector's execution routes and reports the capability `unavailable` until a
release offers it. ms-markets supplies the execution ledger (`OrderManager`, `OrdersTS`,
`TradesTS`) but no execution service.

Hunch does not need an Agent tool: its Jobs and services call the connector over HTTP, and its own
Agent never calls a connector. Alpaca key handling, account identity and the holdings capture
already exist here; order placement is the missing step of the same account workflow.

## Decision

1. **An `execution` capability on the API**, for applications, not for the Agent. The routes are
   the contract Hunch targets:

   | Route | Effect |
   | --- | --- |
   | `POST /v1/accounts/{account_uid}/orders/actions/submit/preflight` | run the pre-trade checks without placing an order |
   | `POST /v1/accounts/{account_uid}/orders/actions/submit` | place a batch of market day orders, idempotent on `client_order_id` |
   | `POST /v1/accounts/{account_uid}/orders/actions/sync` | re-read the batch from Alpaca, record fills, publish holdings |
   | `POST /v1/accounts/{account_uid}/orders/actions/cancel` | cancel the batch's open orders |
   | `GET /v1/accounts/{account_uid}/orders?batch_id=` | the recorded orders, as last synced |

   A submission is `{batch_id, orders: [{client_order_id, asset_identifier, symbol, side,
   quantity, order_type: market, time_in_force: day}], queue_when_closed?}`. Every answer carries
   `{batch_id, account_uid, status, accepted, message?, warnings, holdings_set_uid?, orders: [...]}`
   with one order state per request leg: `client_order_id`, `broker_order_id`, `status`,
   `venue_status`, `filled_quantity`, `average_fill_price`, `submitted_at`, `updated_at`,
   `message`. Order statuses are `submitted`, `partially_filled`, `filled`, `cancelled`,
   `rejected`, `expired` and `failed`; `GET /v1/project-state/capabilities` lists the catalog as
   `features` too, so a consumer probes `{"id": "execution", "status": "available"}`.

2. **Alpaca places the orders with the account's stored credentials**, exactly as the holdings
   capture reads positions: `TradingClient.submit_order` with `MarketOrderRequest`, `qty` (whole
   shares as integers, fractions where Alpaca marks the asset `fractionable`), `time_in_force=day`
   and the caller's `client_order_id`. The asset is the live Alpaca asset behind the canonical
   `ALPACA::<uuid>` identifier; the order is placed under Alpaca's current symbol. No credential,
   Secret name or Alpaca client leaves `alpaca_connectors`.

3. **The preflight reports every check; submit runs only its own** (amended 2026-11-23). The
   two routes do not repeat each other's work. The preflight is the one to call at the start of
   an operation: it places nothing and answers every failed check. Submit refuses a batch only
   for what protects the orders it places and the ledger it writes, which Alpaca cannot enforce.
   It leaves the rest to Alpaca, which refuses each order it won't take.

   | Check | Preflight | Submit | Why submit runs it, or who enforces it |
   | --- | --- | --- | --- |
   | `duplicate_client_order_id` | yes | yes | Each order is placed and read back by its `client_order_id` |
   | `unsupported_order_type`, `unsupported_time_in_force` | yes | yes | Only market day orders are placed |
   | `batch_id_in_use` | yes | yes | The ledger row of another account's batch would take these orders |
   | `account_inactive` | yes | yes | This connector's registration state; Alpaca doesn't know it |
   | Keys open the registered Alpaca account | yes | yes | Orders would go to another account (raised as an error, not a check) |
   | `unknown_asset` | yes | yes | The live Alpaca asset names the symbol the order is placed under |
   | `asset_not_registered` | yes | yes | The `OrdersTS` and `TradesTS` rows key to the registered asset |
   | `account_not_tradable` (status, trading blocked, account blocked, trading suspended) | yes | no | Alpaca refuses each order |
   | `asset_not_tradable` | yes | no | Alpaca refuses the order |
   | `fractional_quantity_not_allowed` | yes | no | Alpaca refuses the order |
   | `market_closed` (unless `queue_when_closed`) | yes | no | Alpaca queues a day order placed while the market is closed |
   | `insufficient_buying_power` (the buys' latest-trade notional net of the sells, skipped with a warning when a price is unavailable) | yes | no | Alpaca refuses the order |

   A failed check is a `409` with `{code, message, retryable: false, checks: [{code, message,
   client_order_id?}]}` from submit, and the same `checks` in the preflight's answer; `code` is
   the one failed check's code, or `pre_trade_checks_failed` when several failed. Alpaca remains
   the authority: an order it refuses at submission is returned as `rejected` with its reason,
   and the rest of the batch proceeds.

4. **Idempotency and order.** A `client_order_id` Alpaca already holds is read back, never placed
   again, so a retried submission cannot double a trade. Sells are placed before buys; when a
   batch has both, the buys wait up to ten seconds for the sells to settle, so a cash account's
   buying power reflects them.

5. **The ms-markets execution ledger** is written by the connector: one `OrderManager` row per
   batch (`unique_identifier = batch_id`, the account as `target_account_uid`, the batch status,
   and the orders' states in `metadata_json`), one `OrdersTS` row per order keyed by its
   submission time, so every sync updates it in place, and one `TradesTS` row per terminal order
   with a fill (Alpaca reports cumulative fills, not executions; `USD` is the settlement and
   commission asset). `OrderEventsTS` is not written: its asset-index contract is defective in
   ms-markets 2.2.12. The runtime attaches `OrderManagerTable`, `OrdersStorage` and
   `TradesStorage` (`account_runtime_models`); they stay ms-markets tables, migrated by the
   ms-markets deployment, never by this project's provider.

6. **A holdings snapshot follows fills.** Whenever a submit or sync records a fill quantity the
   batch did not have before, the existing holdings capture publishes a snapshot; its UID is
   returned as `holdings_set_uid` and kept on the batch. A capture failure is a warning on the
   answer, not an error.

7. **Who may place orders.** The routes need a verified caller like every other route; whose
   account an application may trade is that application's decision (Hunch decides it on its own
   `HunchUserProfileAccount` rows), as it already is for the holdings capture and the account
   update. An application in another repository declares `access.branches:
   [{repository: <this repository>, policy: user}]` on its own workload; nothing is declared here
   for it.

8. **The Alpaca Agent stays out of execution.** No MCP tool, skill or prompt line reaches these
   routes. The Agent's catalog keeps its 17 tools and says so when asked to trade. Giving a
   conversational agent order placement is a separate decision with its own consent design.

## Consequences

- `AGENTS.md`, the capability model, the API and Agent documentation, the prompt and the tests
  that asserted "no trading" now describe this boundary: the API executes for applications, the
  Agent does not.
- Hunch needs no change to use the capability beyond setting `HUNCH_ALPACA_CONNECTOR_URL` and
  declaring `user` on this release; its client already consumes these routes. Hunch runs the
  preflight when it wants a batch refused before anything is placed: since 2026-11-23 submit
  leaves the account's status, the assets' tradability and fractions, the buying power and the
  market hours to Alpaca. In particular, submit places a batch while the market is closed and
  Alpaca queues it for the next session, whatever `queue_when_closed` says; only the preflight
  refuses an unqueued batch on a closed market (`market_closed`).
- Market day orders only. Limit orders, notional orders and extended hours are not offered; a
  request for them is a `422`.
- The ms-markets deployment must have migrated the execution tables (`OrderManager`, `OrdersTS`,
  `TradesTS`), which ms-markets 2.2.x does; the schema gate already waits for the ms-markets
  deployment to reach the version this image pins.
- The preflight's buying-power check needs Alpaca's data API for the latest trade prices; without
  it the check is skipped with a warning and Alpaca decides.
- Tests cover a paper-account submit, sync and cancel round trip against a fake Alpaca trading
  client and an in-memory ledger that validates every frame against the ms-markets `OrdersTS` and
  `TradesTS` storage contracts (`tests/test_execution.py`, `tests/test_orders_api.py`).

## Code

- `src/alpaca_connectors/execution/orders.py`: the contract, the Alpaca status mapping and the
  ledger rows (pure).
- `src/alpaca_connectors/execution/services.py`: pre-trade checks, Alpaca calls, the ledger and
  the holdings snapshot, over injectable collaborators.
- `src/alpaca_connectors/api/app/routers/orders.py` and `services/orders.py`: the routes.
- `src/alpaca_connectors/api/app/capabilities.py`: the `execution` capability and `features`.
