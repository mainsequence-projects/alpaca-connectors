---
name: alpaca-account-workflow
description: Register Alpaca Accounts from existing Secret names imported once or from site-based key entry, replace their keys, and inspect or capture holdings through Alpaca MCP tools.
---

# Alpaca Accounts And Holdings

Discover current schemas with `alpaca__list_tools`, then use `alpaca__call_tool` with `tool`
and `arguments`. Direct MCP clients use those inner names and arguments with `tools/call`.
The API verifies caller/requester identity; never supply a person's UID. When readiness is unknown,
call `alpaca_project_state` with empty arguments. No shell or direct table-write fallback exists.

## Credentials And Mandatory Creation

- Never request, accept, repeat, log or put Alpaca key values into conversation or tools.
  The person enters keys on the Alpaca site, not in chat.
- Existing Main Sequence Secret names the person gives you are supported: the API reads both
  Secrets once, stores the keys encrypted on the Account, and keeps no name. There is no tool
  that lists Secrets; never guess a name. Names beginning `ALPACA_CONNECTORS__` cannot be
  imported.
- `environment: paper | live` is Alpaca brokerage access, not an Organization Environment
  selector. Platform Environment resolution is automatic.
- Creation always registers position Assets, ensures the canonical ms-markets currency Asset if
  missing, and captures initial holdings. These are mandatory. Cash uses `USD`, not an
  Alpaca-specific Asset or `CurrencySpot` pair.
- Deleting a registration deletes its stored keys with the row. Main Sequence Secrets a person
  named are never changed or deleted; historical holdings are retained.

## Site-Based Key Entry

Use `alpaca_account_key_request` operation `start_registration` (brokerage environment,
optional name) or `start_rotation` (existing Account UID). Give the person the returned
`site_url` and `instructions`; never invent a URL or ask them to paste keys in chat.
When they finish, call `status` with the returned `key_request_uid`. Pending means wait;
completed identifies the Account to verify. Expired (after one hour) or cancelled needs a new
request if still wanted. Use `cancel` when the person withdraws a pending request.

Examples are arguments to `alpaca__call_tool`; replace placeholders with returned identifiers:

```json
{"tool":"alpaca_account_key_request","arguments":{"operation":"start_registration","environment":"paper","account_name":"My paper account"}}
```

```json
{"tool":"alpaca_account_key_request","arguments":{"operation":"status","key_request_uid":"KEY_REQUEST_UID"}}
```

## Existing Secret Names

With the Secret names the person gave you, use `alpaca_manage_account` operation `plan_create`
and report blockers. On authorized creation, send the identical request with operation `create`.
The Secret names in this example are placeholders for names the person supplied, not credential
values or global fallbacks:

```json
{"tool":"alpaca_manage_account","arguments":{"operation":"plan_create","request":{"environment":"paper","account_name":"My paper account","api_key_secret_name":"VISIBLE_API_KEY_SECRET_NAME","secret_key_secret_name":"VISIBLE_SECRET_KEY_SECRET_NAME"}}}
```

Verify the returned Account and initial holdings; a registration row alone is not completion.

## Inspect And Maintain

`alpaca_query_accounts` supports `list`, `get`, `holdings`, and `holdings_snapshot`. Account
results report `credential_source` and `credentials_stored`, never a key. Collection limits are
1–100; offsets must be multiples of the fixed limit.
Query holdings only for the selected Account, not every Account in the list:

```json
{"tool":"alpaca_query_accounts","arguments":{"operation":"holdings","account_uid":"ACCOUNT_UID","latest_only":true,"limit":25,"offset":0}}
```

`holdings_snapshot` takes the Account UID and a returned holdings-set UID. Reads never capture
holdings. `alpaca_manage_account` supports `update` (name, active state, or new keys imported
from both Secret names), `refresh` (provider state), and `capture_holdings` (register the complete position
set and publish one snapshot). Send one capture, never one request per position.

Deletion requires the person's exact `DELETE <account_uid>` confirmation. Stop on reference
blockers; do not cascade into linked configurations. Writes can outlast an MCP timeout: inspect
Account/key-request/holdings state before retrying an ambiguous result, never assume rollback.
