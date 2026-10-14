---
name: alpaca-holdings-category
description: Manage ETF sources and Universes, preview and extract components in bulk, and inspect linked category members through Alpaca MCP tools.
---

# ETF Universes And Components

Discover schemas with `alpaca__list_tools`; use `alpaca__call_tool` with `tool` and `arguments`.
Direct MCP clients use the inner names and arguments with `tools/call`. Calls use verified API
identity. When needed, call `alpaca_project_state` with empty arguments for readiness.
No shell or direct MetaTable-write fallback exists.

## Durable Objects

- A user-maintained source describes an ETF target: name, symbol, URL and enabled state.
- A registered Universe links a source and an AssetCategory through durable foreign keys.
  Creation performs no extraction or ETF seed-ticker registration.
- The category holds current registered component Assets, not source identity/configuration.
- An Alpaca `account_uid` is extraction input only, resolving Secret references and registering
  missing constituents. Never assign an Account to a Universe.
- Each successful extraction publishes one complete observed weight frame under the stable
  Universe-backed signal, even when unchanged. Observation time is not guaranteed economic
  effective time.

## Discover And Configure

`alpaca_query_universes` supports `list`, `get`, `members`, `list_sources`, `get_source`,
`preview_source`, and `preview_extraction`. Collection limits are 1–100 and offsets are
multiples of the fixed limit. Source preview is read-only; it creates/infers no Universe.

`alpaca_manage_universe` supports `create_source`, `update_source`, `delete_source`,
and Universe `create`, `update`, `activate`, `deactivate`, `delete`. Universe creation
accepts explicit name, symbol and source URL; the service stores its required links. Use the
person's selected provider URL, not an invented seed.

Examples are arguments to `alpaca__call_tool`; replace UID placeholders with returned UIDs:

```json
{"tool":"alpaca_query_universes","arguments":{"operation":"list","limit":25,"offset":0}}
```

## Extract And Verify

Select a registered Universe and Account, preview the pair and inspect blockers/warnings.
Missing registered Assets are work to do, not a requirement to register every constituent manually.
On authorized extraction, execute the same pair. Extraction runs synchronously in the API; it is
not a bars update or Job submission.

```json
{"tool":"alpaca_query_universes","arguments":{"operation":"preview_extraction","universe_uid":"UNIVERSE_UID","account_uid":"ACCOUNT_UID","timeout":30}}
```

```json
{"tool":"alpaca_manage_universe","arguments":{"operation":"extract_components","universe_uid":"UNIVERSE_UID","account_uid":"ACCOUNT_UID","timeout":30}}
```

Verify returned category linkage, Asset count, signal UID, observation time and weight-row count;
read `members` for the registered Asset list and ETF-signal observations when needed:

```json
{"tool":"alpaca_query_universes","arguments":{"operation":"members","universe_uid":"UNIVERSE_UID","limit":25,"offset":0}}
```

The service resolves one all-status Alpaca catalog plus set-based stored identity lookup,
bulk-registers missing Assets, bulk-replaces members and publishes a batched frame. Never issue
registration or membership calls per component. Non-tradable/inactive status and missing FIGI
are not blockers. A symbol with neither a current Alpaca identity nor one unique stored identity
blocks replacement/publication; preserve existing category and signal state.

Source/Universe deletion requires the person's exact `DELETE <uid>` confirmation. Respect linked
resource blockers; never silently delete bars or signal configurations. After timeout, inspect
members and observations before retrying; an ambiguous response is not rollback.
