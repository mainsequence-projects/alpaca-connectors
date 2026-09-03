# Materialized Universes

Provider-derived universes are stored as ms-markets `AssetCategory` rows and
`AssetCategoryMembership` rows. This application manages categories whose identifiers use the
`HOLDINGS__<SYMBOL>` convention.

A durable `UniverseSource` supplies the symbol and extraction URL. The source and materialized
category remain separate objects, but the category metadata records the source UID used by its Run
action.

## Flow

1. Create the universe configuration with a name, ETF ticker, and explicit source URL.
2. Persist the enabled source and an empty `AssetCategory`; creation performs no extraction.
3. Run the registered universe separately.
4. In Run preflight, extract current holdings through `etfhextractor` and resolve every component
   to exactly one registered Main Sequence Asset.
5. Refuse execution if any component is missing or ambiguous; otherwise replace the memberships.

The browser/API creation route is `POST /v1/universes`. The `/v1/universes/discovery` contract
advertises Run as a separate action with its own preflight.

```bash
alpaca-connectors universe-source preview <SOURCE_UID>
alpaca-connectors universe sync --source-uid <SOURCE_UID>
alpaca-connectors universe sync --source-uid <SOURCE_UID> --execute
alpaca-connectors universe list
alpaca-connectors universe get <CATEGORY_UID>
alpaca-connectors universe delete <CATEGORY_UID>
```

Deletion is explicit and removes the category plus its memberships. It does not delete Assets or the
UniverseSource.

## Lifecycle status

Materialized universes expose an application-owned `is_active` state. The state is stored under the
`alpaca_connectors` namespace in `AssetCategory.metadata_json`, preserving unrelated metadata.
Categories created before this field existed default to active.

Deactivation keeps the category and every membership available for inspection, but prevents that
category from being used as the scope of a new Alpaca market-data update. Activation makes it
eligible again. The `/v1/universes/discovery` contract advertises run, activate, deactivate, and
delete actions; clients must use the advertised preflight and confirmation lifecycle.
