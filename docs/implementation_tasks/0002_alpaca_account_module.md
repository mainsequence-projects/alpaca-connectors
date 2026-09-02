# 0002 - Alpaca Account Storage

> **Status:** implementation complete; the project-owned table was migrated and finalized on
> 2026-09-02. A live account registration was not executed because that would read a real Alpaca
> account and write holdings.
> **Supported runtime:** Python 3.13, `mainsequence>=8.0.7`, `ms-markets>=1.0.2`.

## Purpose

The `src/account/` module registers one Alpaca trading account into the generic ms-markets account
graph and snapshots its current positions and cash as canonical account holdings.

The layers are deliberately separate:

| Layer | Responsibility |
|---|---|
| `src/account/__init__.py` | stable account identity and API-key fingerprint helpers |
| `src/account/alpaca_account_details.py` | project-owned Alpaca detail/current-financials MetaTable |
| `src/account/services.py` | authenticated Alpaca reads and ms-markets writes |
| `src/cli/account.py` | thin `alpaca-connectors account register` command |

## Identity

Alpaca's stable account number survives API-key rotation:

```text
<ACCOUNT_NUMBER>__ALPACA
<ACCOUNT_NUMBER>__ALPACA_PAPER
```

The raw secret is never stored. `api_key_fingerprint` is `sha256(api_key)[:16]` and is used only
for non-reversible audit correlation.

## Storage model

The project owns one table:

`alpaca_connectors__acct_alpaca`

`AlpacaAccountDetails` is keyed one-to-one by `AccountTable.uid` and contains:

- Alpaca account metadata and status flags
- configuration fields such as PDT/DTBP checks and margin/options settings
- the latest current financial values (cash, equity, buying power, margin, fees, and transfers)
- snapshot time and raw account payload

Current financials intentionally live on this one current-state detail row. There is no bespoke
`AlpacaAccountBalancesStorage` time series.

Point-in-time portfolio data uses the ms-markets built-ins:

- `AccountTable`
- `AccountHoldingsSetTable`
- `AccountHoldingsStorage`
- `AssetTable`

Each position becomes a holding keyed by its registered asset unique identifier. Quantity is a
positive magnitude and `direction` carries long/short sign. Provider-specific economics are kept
in `extra_details`. Cash is represented as a holding in the registered cash asset.

## Migration ownership

`AlpacaAccountDetails` belongs to `src.migrations:migration`; core account and holdings tables
remain owned by the ms-markets provider.

The project migration metadata includes `AccountGroupTable` and `AccountTable` only to resolve the
foreign-key graph. The include hook prevents this provider from emitting DDL or catalog
registrations for those core models.

Backend evidence after `mainsequence migrations upgrade --provider src.migrations:migration
head`:

- project table finalized active
- physical table exists
- Alembic revision `0001 (head)`
- runtime attachment expanded and resolved `AccountGroupTable -> AccountTable ->
  AlpacaAccountDetails`

## Execution contract

```bash
alpaca-connectors account register --paper --plan-only
alpaca-connectors account register --paper
```

The account entrypoint attaches `account_runtime_models()` first. Live execution then:

1. loads the Alpaca account, configuration, and positions;
2. upserts the ms-markets `Account`;
3. upserts the project detail/current-financials row;
4. creates or reuses the `AccountHoldingsSet`; and
5. publishes positions and cash to `AccountHoldingsStorage`.

Held equities must resolve strictly to registered ms-markets assets. The default flow can register
missing equities through the connector's existing strict Alpaca plus OpenFIGI path; callers can
disable that behavior.

## Remaining live verification

A safe production verification requires explicit Alpaca account authorization and should confirm:

- expected account identity and paper/live environment
- no raw API key or secret in stored payloads/logs
- one active detail row for the account
- one holdings set for the requested snapshot time
- equity and cash holdings use registered asset unique identifiers
- rerunning the same snapshot is idempotent

This remaining check is data execution, not schema migration; the backend schema migration itself
is complete.
