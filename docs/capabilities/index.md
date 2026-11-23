# Capability Model

The repository is organized around the market and account capabilities it already implements.
Provider integrations support these capabilities; they are not a separate `Connection` domain.

## Project State

Project State exposes the health and configuration currently available to repository consumers:

- API health
- authenticated request-user UID when the platform supplies it
- migrated price dataset identities
- the account-registration count
- the current capability catalog

It is an aggregate control view, not a persisted business entity.

## Assets

Assets owns canonical instrument identity and registration:

- Alpaca symbols and availability
- OpenFIGI resolution
- Main Sequence asset identity
- the trading calendar of each asset's Alpaca class
- registration plans, eligibility, blockers, and execution

An application registers exactly the symbols it needs, for example the assets of a portfolio it
is about to give weights.

## Market Data

Market Data owns Alpaca observations and update behavior:

- OHLCV bar datasets
- cadence, feed, and adjustment
- selected assets, an account's recent holdings, or every asset of the portfolios that use it
- update planning and execution
- asset-indexed Main Sequence storage

## Accounts

Accounts owns brokerage account identity and account-level state:

- stable account identity
- paper or live environment
- Alpaca account details
- status, balances, margin, and buying power
- managed or external credential Secret references, account registration, CRUD, and refresh

Individual positions are part of Holdings. Account refresh and holdings capture are separate
lifecycle operations.

## Holdings

Holdings represents an account's asset exposure at a point in time: the brokerage positions and
cash of an account, published as immutable ms-markets snapshots. The account sync captures them,
sizes the account's target and allocates the snapshot to the account's funds
([ADR 0023](../adrs/0023_account_sync_sizes_and_allocates_funds.md)).

## Portfolios

Portfolios owns analytical construction from weights an application provides, on registered
assets and market data ([Portfolios](../portfolios/portfolios.md)):

- portfolio identity and its owner, the acting owner of the call that created it (ADR 0024)
- the provided weights, as an ms-markets `ApplicationWeights` signal
- price-source binding to a bars configuration
- persistent interpolated prices
- reusable calendar-event rebalance configurations
- portfolio weights and valuation results
- runs on the portfolio Job declared in the API workflow, with a per-configuration scheduled flag
- retirement of the `Portfolio` when a portfolio is deleted

It is the connector's only kind of portfolio ([ADR 0027](../adrs/0027_one_portfolio_path.md)).

## Execution

Execution places batches of market orders on a registered account for authorised applications
([ADR 0016](../adrs/0016_order_execution_for_authorised_applications.md)):

- batches keyed by the caller's `batch_id`, orders by the caller's `client_order_id`
- market day orders, whole shares or fractions where Alpaca allows them
- a preflight that reports every pre-trade check without placing anything, and a submit that
  refuses (`409`) only for what protects its orders and the ledger, leaving the rest to Alpaca
- the ms-markets execution ledger: `OrderManager`, `OrdersTS`, `TradesTS`
- a holdings snapshot after fills

It is an API capability. The Alpaca Agent has no tool over it and places no orders.

## Operations

Operations is the secondary execution view shared by the capabilities:

- dry-run plans
- mutations explicitly requested by the user
- validation blockers
- execution results and failures
- existing manual or scheduled invocation paths

A Job is one possible invocation mechanism. It is not the primary product ontology.

## Public Code Boundaries

Use these capability-oriented imports:

- `alpaca_connectors.assets`
- `alpaca_connectors.market_data`
- `alpaca_connectors.account`
- `alpaca_connectors.holdings`
- `alpaca_connectors.execution`
- `alpaca_connectors.portfolios`
- `alpaca_connectors.api.app.capabilities` for the API-only descriptive catalog
