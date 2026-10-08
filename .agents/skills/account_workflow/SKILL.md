---
name: alpaca-account-workflow
description: Register Alpaca Accounts from keys the person enters on the Alpaca site (or from existing Main Sequence Secret names), replace their keys, and inspect or capture their ms-markets holdings snapshots.
---

# Alpaca Account Workflow

Use this skill for Alpaca Account registration, key replacement, account refresh, and holdings
snapshots. In an Agent session, call `alpaca_query_accounts`, `alpaca_manage_account`, and
`alpaca_account_key_request` through `alpaca__call_tool`; they run in the API as the person you
serve. In a shell, use the installed `alpaca-connectors` command.

## Credential Boundary

- Never ask for, accept, return, log, or persist Alpaca key values: anything typed in the
  conversation or passed to a tool stays in the session history. No tool accepts them.
- When the person wants to register an account or replace an account's keys with keys they hold,
  use `alpaca_account_key_request` (ADR 0015). The person types the keys on the Alpaca site; the
  API stores them as application-managed Main Sequence Secrets (ADR 0011).
- An account can also be registered from two existing Main Sequence Secrets the person names
  (`alpaca_manage_account`). Names starting with `ALPACA_CONNECTORS__` are application-managed
  Secrets and cannot be selected. An account's `credential_source` is `managed` or `external`;
  deleting a managed account also deletes its credential Secrets, while external Secrets are never
  modified or deleted.
- The person selects `paper` or `live`; deployed Organization Environment resolution remains
  automatic and is never a tool input.
- Account creation always resolves the provider Account, registers every position Asset plus the
  canonical ms-markets currency Asset when needed, and creates the initial holdings snapshot. These
  steps are mandatory, not options.
- Cash uses the ms-markets currency Asset such as `USD`; do not create an Alpaca-specific cash Asset
  or a `CurrencySpot` pair.

## Register Or Replace Keys On The Site

1. Call `alpaca_account_key_request` with operation `start_registration` (`environment`, optional
   `account_name`) or `start_rotation` (`account_uid`). The request belongs to the person you serve.
2. Give the person the returned `site_url` and `instructions`: open the Alpaca site, go to
   Accounts, and enter the API key and secret key for the pending request. Do not ask for the keys.
3. When the person says they are done, call operation `status` with the `key_request_uid`.
   `completed` carries the `account_uid`; verify it with `alpaca_query_accounts` operation `get`
   and report the initial holdings. `expired` (after one hour) or `cancelled` means starting a new
   request if the person still wants it. Use `cancel` when they change their mind.

## Register From Existing Secret Names

1. Use query operation `secret_references` to show only visible Secret names when the person needs
   to choose them.
2. Use manage operation `plan_create` before creation and report provider/account blockers.
3. Use operation `create` only when authorized. Verify the Account and initial holdings result.

## Maintain Accounts

1. Query one Account with operation `get`; query holdings only for that selected `account_uid` using
   operation `holdings` with `latest_only: true` when the latest snapshot is wanted.
2. Use `update`, `refresh`, or `capture_holdings` as requested. Holdings capture must register the
   complete position set and publish one immutable snapshot.

Shell equivalents:

```shell
alpaca-connectors account list
alpaca-connectors account register --api-key-secret-name <API_KEY_SECRET> --secret-key-secret-name <SECRET_KEY_SECRET>
alpaca-connectors account refresh <ACCOUNT_UID>
alpaca-connectors holdings list --account-uid <ACCOUNT_UID>
alpaca-connectors holdings capture --account-uid <ACCOUNT_UID>
alpaca-connectors holdings capture --account-uid <ACCOUNT_UID> --execute
```

Deletion removes the connector registration while retaining historical holdings. The MCP tool
requires exact `DELETE <account_uid>` confirmation and must stop when a signal configuration still
references the Account.
