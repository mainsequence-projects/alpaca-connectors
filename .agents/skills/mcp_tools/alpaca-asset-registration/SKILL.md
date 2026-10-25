---
name: alpaca-asset-registration
description: Register exact Alpaca US equity symbols as canonical Main Sequence Assets, using a registered Account's stored keys and optional OpenFIGI enrichment.
---

# Alpaca Asset Registration

Use this skill to query registered Assets or register an exact symbol set. The tools run in the
Alpaca API with verified caller/requester identity. Use `alpaca__list_tools` for their current
schemas and `alpaca__call_tool` with `tool` and `arguments`; a direct MCP client uses the same
inner tool name and arguments with `tools/call`. There is no shell or direct table-write fallback.

## Inspect And Select

For project readiness, call `alpaca_project_state` with empty arguments. Select a registered
Account with `alpaca_query_accounts` operation `list` or `get`; never invent its UID or ask for
credential values. Query Assets with `alpaca_query_assets`: `list` accepts `search`, `category_uid`,
`ordering`, `limit` (1–100), and `offset`; `get` requires `asset_uid`. Page only as needed, keeping
`limit` fixed and increasing `offset` by it.

Examples below are arguments to `alpaca__call_tool`; replace UID placeholders with returned UIDs:

```json
{"tool":"alpaca_query_assets","arguments":{"operation":"list","search":"AAPL","limit":25,"offset":0}}
```

## Identity And Inputs

- Require a registered `account_uid`; its stored keys, opened only by the API, give provider
  credentials at runtime. Never request or accept API-key values.
- Require explicit symbols. Asset registration never expands ETF seeds or extracts components.
- Treat Alpaca's immutable asset UUID as authoritative:
  `Asset.unique_identifier = ALPACA::<alpaca_asset_uuid>`.
- Require one connector-owned `AlpacaAssetDetails` row for each Alpaca Asset.
- Preserve Alpaca `status` and `tradable` as facts, not eligibility gates.
- Treat OpenFIGI as optional enrichment. Missing FIGI data or an OpenFIGI outage never blocks an
  otherwise valid Alpaca registration.

Send the complete symbol set in one registration request, not one tool call per symbol. The
service resolves one all-status catalog, bulk-registers canonical Assets and required Alpaca
details, and publishes one batched metadata snapshot. It handles current-catalog misses using
one unique previously stored Alpaca identity; do not invent an identity or drop a blocker.

## Workflow

1. Call `alpaca_register_assets` with operation `plan` and a request containing `account_uid`,
   `symbols`, and optional `timeout`.
2. Inspect whether registration is allowed; report unresolved Alpaca symbols separately from
   non-blocking OpenFIGI warnings. A missing registered Asset is work to do, not itself a blocker.
3. Execute only when the user requested registration, using the identical request with operation
   `execute`.
4. Report returned existing/created Asset UIDs and symbols not registered. Verify selected Assets
   with `get` when needed; do not fetch every Asset again solely to count them.

Read-only plan:

```json
{"tool":"alpaca_register_assets","arguments":{"operation":"plan","request":{"account_uid":"ACCOUNT_UID","symbols":["AAPL","NVDA"],"timeout":30}}}
```

Authorized execution uses the same request:

```json
{"tool":"alpaca_register_assets","arguments":{"operation":"execute","request":{"account_uid":"ACCOUNT_UID","symbols":["AAPL","NVDA"],"timeout":30}}}
```

Stop if the account does not exist, its stored keys cannot be opened, Alpaca supplies no
immutable asset UUID, or a symbol has neither one current-catalog identity nor one unique stored
Alpaca identity. A timeout or ambiguous transport result is not proof that a write failed; inspect
the requested Assets before repeating execution. This workflow registers Assets, not orders.
