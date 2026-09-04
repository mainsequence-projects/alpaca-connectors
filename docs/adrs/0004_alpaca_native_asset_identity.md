# 0004: Alpaca-Native Asset Identity

Status: Accepted

## Context

The connector previously treated an OpenFIGI match as both the identity source and a registration
requirement. That made an optional classification service a blocker for Alpaca assets, account
positions, universes, and bar updates. It also discarded Alpaca's immutable asset UUID even though
that UUID is the provider's authoritative identifier.

`AssetTable` is intentionally provider-neutral and small. Provider-specific attributes belong in
one-to-one detail tables keyed by `AssetTable.uid`.

## Decision

Every asset registered by this connector uses:

```text
Asset.unique_identifier = ALPACA::<canonical-lowercase-alpaca-asset-uuid>
```

Every such asset must have exactly one project-owned `AlpacaAssetDetails` row. Its `asset_uid` is
the primary key and foreign key to `AssetTable.uid`; `alpaca_asset_id` is non-null and globally
unique in that table. Symbol, exchange, status, and Alpaca trading flags are provider attributes,
not identity keys. The typed row API exposes its generic `uid` property as a validation alias of
`asset_uid`; the physical table does not add a second UID column.

`OpenFigiAssetDetails` remains optional enrichment. Missing, ambiguous, timed-out, or failed
OpenFIGI lookups produce visible warnings but never block Alpaca asset registration. Missing or
unsupported Alpaca identity does block execution before any asset write.

Account registration always resolves or registers every non-zero held position by Alpaca asset
UUID and creates the initial holdings snapshot in the same flow. Registry resolution completes
before either the Account or snapshot is written. Later holdings captures use the same hard
registry. Currency assets such as USD retain their shared ms-markets identity and are not fabricated
as Alpaca assets. Account planning reports a missing canonical currency as an asset that execution
will ensure; registration and later capture idempotently upsert the built-in currency AssetType and
the single-currency Asset before publishing cash. They do not create Alpaca details or CurrencySpot
pair metadata for that row.

Alpaca crypto positions are a provider-specific exception to direct position/catalog UUID equality:
the position UUID can differ from the Asset catalog UUID for the same pair. Crypto holdings resolve
the position symbol through the Alpaca Asset endpoint, validate equivalent old/canonical symbology
such as `BTCUSD` and `BTC/USD`, and use the returned catalog UUID as the canonical Asset identity.
For non-crypto holdings, position and catalog UUID equality remains mandatory.

There is no compatibility read path, automated backfill, or identifier rewrite. Existing
connector-owned FIGI-keyed rows and their dependants are manually deleted from leaves to roots,
then recreated through the Alpaca-native workflows. The Alembic revision that creates
`AlpacaAssetDetails` is a schema migration, not a legacy-data migration.

## Consequences

- Symbol changes do not change asset identity.
- Bars, holdings, universes, and portfolio workflows resolve the same canonical `Asset.uid`.
- Catalog and API searches join required Alpaca details and expose FIGI only when enrichment exists.
- Old FIGI-keyed data remains deliberately unreadable by the new connector path until it is
  manually removed.
- Writers must remain paused during the reviewed data reset and may resume only after schema and
  code deployment.
