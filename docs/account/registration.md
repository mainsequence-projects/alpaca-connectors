# Account Registration

Register an **Alpaca trading account** into the ms-markets account layer and snapshot its balances
and holdings.

Main module:

- `src/account/` (`__init__.py` identity, `alpaca_account_details.py` storage, `services.py` flow)

Primary CLI:

- `alpaca-connectors account register`

## Identity

The ms-markets `Account.unique_identifier` is the stable Alpaca `account_number` plus a venue +
environment suffix:

```text
<account_number>__ALPACA          # live
<account_number>__ALPACA_PAPER    # paper
```

Using `account_number` (returned by `GET /v2/account`) instead of an API-key hash keeps identity
stable across API-key rotation. The non-reversible `sha256(api_key)[:16]` fingerprint is stored on
the detail row for audit; the raw key/secret is never persisted.

## What gets written

| Target | Kind | Contents |
|---|---|---|
| `msm.api.accounts.Account` | ms-markets row | identity, `account_name`, `is_paper`, active flag |
| `AlpacaAccountDetails` (`src/account/alpaca_account_details.py`) | project detail MetaTable (one row/account, PK+FK `account_uid`) | static metadata (status, margin multiplier, PDT flag, shorting/blocks, options levels, account configuration, api-key fingerprint) **and the current financials** (cash, equity, buying-power family, margin, SMA, fees, daytrade count — incl. the six live-API-only fields from the raw payload), refreshed on each register |
| `AccountHoldingsStorage` (ms-markets) | the **canonical** account-holdings store | one row per equity position **plus a USD cash row**; positive `quantity` + `direction`; per-position economics in `extra_details` |

Point-in-time portfolio data uses the ms-markets `AccountHoldingsStorage` directly — the connector
does **not** add a bespoke balances time-series table. The account-level financials that are not
per-asset holdings (equity, buying-power, margin, …) are kept as current values on the detail row.

Equity positions resolve to an `Asset.unique_identifier` (FIGI) via the project's OpenFIGI
resolution. By default a held equity that resolves to a **FIGI** but is not yet registered is
**auto-registered** (strict FIGI path) so the account snapshot stays current without forcing you to
pre-register every position; pass `--no-register-missing-assets` to disable. A held equity with **no
FIGI** is never created — it is reported as `unresolved_symbols`. Cash references a **pre-existing**
`USD` currency asset (FIGI-less, so reused from the Asset table, never created); if it is absent the
cash row is skipped. Non-equity positions (options/crypto) are skipped in v1 and reported as
`skipped_non_equity_symbols`.

## Prerequisites

The same storage-first prerequisites as the rest of the project:

1. Migrate the project tables (includes `AlpacaAccountDetails` + `AlpacaAccountBalancesStorage`):
   `mainsequence migrations upgrade --provider src.migrations:migration head`.
2. The runtime is attached automatically (`src.runtime.account_runtime_models()` via
   `start_markets_engine`). The ms-markets built-in account tables (`Account`,
   `AccountHoldingsSet`, `AccountHoldingsStorage`) are migrated by ms-markets' own provider.

## Credentials

`ALPACA_API_KEY` / `ALPACA_SECRET_KEY` from the environment first, then MainSequence secrets. The
secret is never a CLI flag.

## CLI

Dry run (reads the account, prints the plan, writes nothing):

```bash
alpaca-connectors account register --paper --plan-only
```

Execute against the paper account:

```bash
alpaca-connectors account register --paper
```

Live account:

```bash
alpaca-connectors account register --no-paper
```
