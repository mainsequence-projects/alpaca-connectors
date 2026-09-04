# Universe Sources

A `UniverseSource` is an explicit provider extraction configuration. It is not an Asset Universe,
Asset, fund, Portfolio, or AssetCategory. A registered `AssetUniverse.source_uid` references it with
a required foreign key; no source is inferred from a symbol, provider, category, or runtime
configuration.

| Field | Meaning |
| --- | --- |
| `uid` | application-generated UUID identity |
| `name` | user-facing source name |
| `symbol` | normalized extraction symbol |
| `source_url` | absolute URL read by `etfhextractor` |
| `enabled` | whether preview and linked Universe Runs are allowed |
| `created_at`, `updated_at` | UTC lifecycle timestamps |

The source table replaces Python ticker lists and ticker/provider maps. Users can create, update,
disable, and delete unreferenced sources without changing code. Symbol changes and deletion are
blocked once an Asset Universe references the source, preserving the registered relationship.

## Migration And Seed

```bash
mainsequence migrations upgrade --provider src.migrations:migration head
alpaca-connectors universe-source seed-defaults
```

The seed command creates one IVV starter source using a fixed UUID and is idempotent. It runs only
when explicitly called and points to the official US iShares IVV product page. It does not create
an Asset Universe or category.

## CRUD And Preview

```bash
alpaca-connectors universe-source list
alpaca-connectors universe-source get <SOURCE_UID>
alpaca-connectors universe-source create \
  --name "S&P 500 source" --symbol IVV --url "https://example.com/fund"
alpaca-connectors universe-source update <SOURCE_UID> --no-enabled
alpaca-connectors universe-source delete <SOURCE_UID>
alpaca-connectors universe-source delete <SOURCE_UID> --execute
alpaca-connectors universe-source preview <SOURCE_UID>
```

Preview is read-only: it extracts and validates without registering a universe or writing category
memberships. Universe creation is the explicit `POST /v1/universes` application path. Once created,
Run uses the registered Universe UID through `alpaca-connectors universe run <UNIVERSE_UID>` or the
API discovery action.
