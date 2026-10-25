# ADR: Order Execution For Authorised Applications

## Status

Accepted on 2026-10-26. Supersedes the "no trading, ordering" rule that `AGENTS.md`, the
[capability model](../capabilities/index.md) and [ADR 0015](0015_agent_tools_on_the_api_mcp_and_site_key_entry.md)
stated for this repository: the API now places orders. The Alpaca Agent still does not; that part
of ADR 0015 stands.

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

3. **Pre-trade checks refuse a batch as a structured `409`** before any order is placed:
   `account_inactive`, `account_not_tradable` (Alpaca status, trading blocked, account blocked,
   trading suspended), `unknown_asset`, `asset_not_registered`, `asset_not_tradable`,
   `fractional_quantity_not_allowed`, `market_closed` (unless the caller sets
   `queue_when_closed`, which lets Alpaca queue the day orders for the next session),
   `insufficient_buying_power` (the buys' latest-trade notional net of the sells against Alpaca's
   `buying_power`, skipped with a warning when a price is unavailable), `duplicate_client_order_id`,
   `unsupported_order_type`, `unsupported_time_in_force`, `batch_id_in_use`. The error body is
   `{code, message, retryable: false, checks: [{code, message, client_order_id?}]}`; `code` is the
   one failed check's code, or `pre_trade_checks_failed` when several failed. Alpaca remains the
   authority: an order it refuses at submission is returned as `rejected` with its reason, and the
   rest of the batch proceeds.

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
  declaring `user` on this release; its client already consumes these routes. Hunch should decide
  whether to send `queue_when_closed` for approvals outside market hours, since the default
  refuses them with `market_closed`.
- Market day orders only. Limit orders, notional orders and extended hours are not offered; a
  request for them is a `422`.
- The ms-markets deployment must have migrated the execution tables (`OrderManager`, `OrdersTS`,
  `TradesTS`), which ms-markets 2.2.x does; the schema gate already waits for the ms-markets
  deployment to reach the version this image pins.
- The buying-power check needs Alpaca's data API for the latest trade prices; without it the
  check is skipped with a warning and Alpaca decides.
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
