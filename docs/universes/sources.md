# Universe Sources

A `UniverseSource` is the durable user-maintained definition of a provider-derived universe. It is
not an Asset, fund, Portfolio, or AssetCategory foreign key.

| Field | Meaning |
| --- | --- |
| `uid` | application-generated UUID identity |
| `name` | user-facing source name |
| `symbol` | normalized symbol used for the materialized category name |
| `source_url` | absolute URL read by `etfhextractor` |
| `enabled` | whether preview and sync are allowed |
| `created_at`, `updated_at` | UTC lifecycle timestamps |

The source table replaces the old Python ticker lists and ticker/provider map. Users can create,
update, disable, and delete sources without changing code.

## Migration And Seed

```bash
mainsequence migrations upgrade --provider src.migrations:migration head
alpaca-connectors universe-source seed-defaults
```

The seed command creates one IVV starter source using a fixed UUID and is idempotent. It runs only
when explicitly called and points to the official US iShares IVV product page. If the packaged
starter metadata changes, rerunning the command updates the same row in place; it does not create a
second source or rewrite user-created rows.

## CRUD And Materialization

```bash
alpaca-connectors universe-source list
alpaca-connectors universe-source get <SOURCE_UID>
alpaca-connectors universe-source create \
  --name "S&P 500 source" --symbol IVV --url "https://example.com/fund"
alpaca-connectors universe-source update <SOURCE_UID> --no-enabled
alpaca-connectors universe-source delete <SOURCE_UID>
alpaca-connectors universe-source delete <SOURCE_UID> --execute

alpaca-connectors universe-source preview <SOURCE_UID>
alpaca-connectors universe sync --source-uid <SOURCE_UID>
alpaca-connectors universe sync --source-uid <SOURCE_UID> --execute
```

Preview extracts and validates without writes. Sync refuses to change category membership while any
component is missing or ambiguous in Main Sequence.

Deleting a source does not delete its materialized category. Materialized managed categories are
listed and removed separately through `alpaca-connectors universe` or `/v1/universes`.
