---
name: alpaca-account-workflow
description: Register and maintain Alpaca Accounts using Main Sequence Secret names, and inspect or capture their ms-markets holdings snapshots.
---

# Alpaca Account Workflow

Use this skill for Alpaca Account registration, account refresh, and holdings snapshots. In a
CodeRepository Executor session, use `alpaca_query_accounts` and `alpaca_manage_account`. In a
shell, use the installed `alpaca-connectors` command.

## Credential Boundary

- The user supplies the names of the Main Sequence Secrets containing the Alpaca API key and secret
  key. Never accept, return, log, or persist the credential values.
- The user selects `paper` or `live`; deployed Organization Environment resolution remains automatic
  and is never a tool input.
- Account creation always resolves the provider Account, registers every position Asset plus the
  canonical ms-markets currency Asset when needed, and creates the initial holdings snapshot. These
  steps are mandatory, not options.
- Cash uses the ms-markets currency Asset such as `USD`; do not create an Alpaca-specific cash Asset
  or a `CurrencySpot` pair.

## Workflow

1. Use query operation `secret_references` to show only visible Secret names when the user needs to
   choose them.
2. Use manage operation `plan_create` before creation and report provider/account blockers.
3. Use operation `create` only when authorized. Verify the Account and initial holdings result.
4. Query one Account with operation `get`; query holdings only for that selected `account_uid` using
   operation `holdings` with `latest_only: true` when the latest snapshot is wanted.
5. Use `update`, `refresh`, or `capture_holdings` as requested. Holdings capture must register the
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

Deletion removes the connector registration while retaining historical holdings. The Tau tool
requires exact `DELETE <account_uid>` confirmation and must stop when a signal configuration still
references the Account.
