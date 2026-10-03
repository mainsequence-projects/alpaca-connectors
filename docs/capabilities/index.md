# Capability Model

The repository is organized around the market and account capabilities it already implements.
Provider integrations support these capabilities; they are not a separate `Connection` domain.

## Project State

Project State exposes the health and configuration currently available to repository consumers:

- API health
- authenticated request-user UID when the platform supplies it
- migrated price dataset identities
- account-registration and universe-source counts
- the current capability catalog

It is an aggregate control view, not a persisted business entity.

## Assets

Assets owns canonical instrument identity and registration:

- Alpaca symbols and availability
- OpenFIGI resolution
- Main Sequence asset identity
- registration plans, eligibility, blockers, and execution

## Universes

Universes owns reusable collections of registered assets:

- durable user-maintained extraction sources
- provider-derived constituent extraction
- asset-registration coverage
- strict membership validation
- `AssetCategory` creation and refresh

ETF holdings are one supported source of universe membership. They do not define the entire
repository architecture.

## Market Data

Market Data owns Alpaca observations and update behavior:

- OHLCV bar datasets
- cadence, feed, and adjustment
- selected assets or an asset universe
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

Holdings represents asset exposure at a point in time. The repository currently uses two forms:

- persisted brokerage positions and cash associated with an account
- provider-derived fund composition consumed by Universes and Portfolios

The second form is currently extracted for downstream use rather than persisted as a
project-owned holdings registry.

## Portfolios

Portfolios owns analytical construction based on registered assets and market data:

- portfolio identity
- resolved component weights
- price-source binding
- persistent interpolated prices
- reusable calendar-event rebalance configurations
- durable calculation configurations composed from existing Signal and Bars configurations
- portfolio weights and valuation results
- one dedicated scheduled Job per Portfolio Configuration

The repository does not provide order placement or trade execution.

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
- `alpaca_connectors.universes`
- `alpaca_connectors.market_data`
- `alpaca_connectors.account`
- `alpaca_connectors.holdings`
- `alpaca_connectors.portfolios`
- `alpaca_connectors.api.app.capabilities` for the API-only descriptive catalog
