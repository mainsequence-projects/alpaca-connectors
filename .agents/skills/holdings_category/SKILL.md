---
name: alpaca-holdings-category
description: Manage user-defined ETF extraction sources and registered Asset Universes, then extract components into linked AssetCategories and observed ETF weight signals.
---

# Holdings-Backed Asset Universes

Use this skill for Universe source CRUD, registered Universe CRUD, linked AssetCategory membership,
and component extraction. In a CodeRepository Executor session, use `alpaca_query_universes` and
`alpaca_manage_universe`. In a shell, use the installed `alpaca-connectors` command.

## Model

- `UniverseSource` stores one user-maintained ETF extraction target: name, symbol, URL, and enabled
  state.
- `AssetUniverse` links one explicit source to one explicit `AssetCategory` through durable foreign
  keys. Creation performs no extraction.
- A registered Alpaca account is required only when extracting components so its Secret names can
  resolve Alpaca and missing constituents can be registered. Never persist or infer that Account on
  the Universe.
- The linked category owns current membership. It does not own source configuration.
- Every successful extraction publishes that exact observed weight frame through the stable
  Universe-backed `AlpacaETFHoldingsSignal`. Observation time does not guarantee exact economic
  effective time.

## Workflow

1. Read or maintain explicit sources through the `list_sources`, `get_source`, `create_source`,
   `update_source`, and `delete_source` operations.
2. Create a registered Universe from explicit name, symbol, and source URL. Do not extract during
   creation.
3. Preview with `alpaca_query_universes` operation `preview_extraction`, supplying `universe_uid`
   and runtime `account_uid`.
4. When authorized, call `alpaca_manage_universe` operation `extract_components` with the same UIDs.
5. Verify one complete bulk category replacement, one batched signal observation, and the linked
   category members via operation `members`.

Shell equivalents:

```shell
alpaca-connectors universe-source list
alpaca-connectors universe-source preview <SOURCE_UID>
alpaca-connectors universe list
alpaca-connectors universe run --universe-uid <UNIVERSE_UID> --account-uid <ACCOUNT_UID>
alpaca-connectors universe run --universe-uid <UNIVERSE_UID> --account-uid <ACCOUNT_UID> --execute
```

Use `AssetCategory.replace_memberships` for the complete desired set. Resolve and register missing
Alpaca constituents in bulk before replacing membership. If any symbol cannot resolve to one
canonical Alpaca Asset, stop and preserve the existing category and signal state.

Deletion through the Tau tool requires exact `DELETE <uid>` confirmation and must stop when bar or
signal configurations reference the Universe.
