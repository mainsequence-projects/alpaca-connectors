# ADR: Stored Alpaca Bars Configurations And Asset Sources

## Status

Accepted — implemented in migration `0004`

## Context

Alpaca bars updates currently accept an output `dataset_uid` and construct an
`AlpacaStockBarsConfig` directly from request or CLI arguments. That exposes an internal
`TimeIndexMetaTable.uid` to users and leaves no durable application object that users can create,
review, edit, disable, or reuse.

The desired workflow is:

```text
configuration input
  -> stored Alpaca bars configuration
  -> configuration resolution
  -> Alpaca bars update process
```

The stored configuration must also support three different sources for the assets included in an
update:

1. an explicit set of registered Assets;
2. a registered universe (`AssetCategory`);
3. the latest stored holdings of the configured Alpaca Account.

The third source must not require another account or holdings-set identifier from the user. The
configuration's existing `account_uid` is both the Alpaca credential source and, when
`asset_source=account_holdings`, the identity used to find the latest holdings snapshot.

## Decision

Introduce a durable `AlpacaBarsConfiguration` application concept backed by a project-owned,
migration-managed MetaTable. One row is one named, reusable definition of an Alpaca stock-bars
update.

The update API, CLI, and scheduled launchers will execute a stored configuration by its UUID. They
will not accept an output `dataset_uid` and will not construct an ephemeral business configuration
directly from frequency, feed, adjustment, account, and asset-scope arguments.

The output MetaTable remains a derived implementation detail. A resolver will map the stored
`frequency_id`, `feed`, and `adjustment` to exactly one already-migrated Alpaca bars storage class
and its registered `TimeIndexMetaTable`.

## Stored Configuration Contract

`AlpacaBarsConfigurationTable` will contain:

| Column | Contract |
| --- | --- |
| `uid` | Generated UUID primary key and sole durable configuration identity. |
| `name` | Required user-facing name. It is presentation metadata, not update identity. |
| `description` | Optional user-facing explanation. |
| `enabled` | Whether resolution and execution are allowed. |
| `account_uid` | Required FK to the registered ms-markets `AccountTable.uid`. |
| `asset_source` | Required discriminator: `assets`, `universe`, or `account_holdings`. |
| `universe_uid` | Nullable FK to project-owned `AssetUniverseTable.uid`; populated only for `universe`. |
| `frequency_id` | Normalized Alpaca bars frequency. |
| `feed` | Normalized Alpaca data feed. |
| `adjustment` | Normalized Alpaca adjustment mode. |
| `created_at` | UTC creation timestamp. |
| `updated_at` | UTC timestamp of the latest configuration change. |

There will be no configuration `unique_identifier`. The UUID primary key is sufficient.

The table will not persist:

- an output `dataset_uid` or `TimeIndexMetaTable.uid`;
- a physical output-table name;
- Main Sequence Secret values;
- a DataNode `update_hash`;
- `hash_namespace`;
- removed SDK run switches such as `force_update` or `debug_mode`.

Explicit multi-asset selections will be normalized into
`AlpacaBarsConfigurationAssetTable`, keyed by `(configuration_uid, asset_uid)`, rather than stored
as an unvalidated JSON array. Deleting a configuration cascades only to these membership rows.
Deleting a configuration never deletes bars observations or historical platform update-process
records.

## Asset-Source Contract

### `assets`

- `universe_uid` must be null.
- At least one `AlpacaBarsConfigurationAssetTable` row must exist.
- Every member must reference an existing `AssetTable.uid`.
- Resolution converts those rows to canonical `Asset.unique_identifier` values.
- The explicit resolved asset list participates in the DataNode update hash.

### `universe`

- `universe_uid` is required.
- Explicit configuration-asset membership rows are forbidden.
- Resolution loads the referenced active `AssetUniverse`, follows its required
  `asset_category_uid`, and verifies that the linked category currently contains assets.
- The runtime `AlpacaStockBarsConfig` uses the category's stable `unique_identifier`, rather than
  freezing the current members into `asset_list`.
- Universe membership may change without editing the bars configuration or changing its updater
  identity. Newly added assets use their own incremental update statistics.

### `account_holdings`

- `universe_uid` must be null.
- Explicit configuration-asset membership rows are forbidden.
- The existing configuration `account_uid` is the only scope identifier supplied by the user.
- Resolution reads persisted ms-markets account holdings. It does not call Alpaca to capture a new
  holdings snapshot as a side effect.

For deterministic freshness behavior, "within one month" means a trailing 30-day UTC window. At
resolution time `T`, the resolver must:

1. Query `AccountHoldingsSetTable` for the configuration's `account_uid` where
   `T - 30 days <= time_index <= T`.
2. Select the row with the greatest `time_index`.
3. Read `AccountHoldingsStorage` rows for that exact `holdings_set_uid` and `account_uid`.
4. Keep distinct held asset identifiers with a non-zero position.
5. Exclude the cash holding because Alpaca stock bars do not exist for the cash balance.
6. Require every remaining identifier to resolve to one registered Main Sequence Asset.

Long and short equity positions are both in scope. Position direction and quantity do not affect
whether an asset needs bars.

Resolution is blocked when:

- no holdings set exists inside the trailing 30-day window;
- the selected holdings set has no non-cash equity assets;
- a holdings row references a missing Main Sequence Asset;
- a non-cash held identifier does not resolve to a registered Main Sequence Asset.

Execution additionally blocks if one of those registered assets does not resolve uniquely to a
currently available Alpaca US-equity symbol. That check contacts Alpaca and therefore does not run
inside the pure review stage.

The resolver must not silently use an older holdings set, merge rows from several holdings sets,
capture fresh holdings automatically, or silently discard unresolved non-cash holdings. The
failure response must instruct the operator to capture account holdings before retrying when the
snapshot is missing or stale.

`AccountHoldingsStorage` is a read-only upstream of this scope. The bars updater must expose it as
an explicit table dependency/reference when `asset_source=account_holdings`; selecting holdings
does not schedule or run the holdings producer automatically.

## Configuration And Update Identity

The runtime `AlpacaStockBarsConfig` will represent the selected asset source explicitly. Its
hashed business inputs are:

| Asset source | Hashed scope identity |
| --- | --- |
| `assets` | Explicit canonical asset identifiers. |
| `universe` | Stable linked `AssetCategory.unique_identifier`, resolved through the stored Universe UID. |
| `account_holdings` | Stable Account UID as holdings-source identity. |

`frequency_id`, `feed`, `adjustment`, and `asset_source` also participate in the update hash.

The stored configuration UID, display name, description, and enabled state do not participate in
the update hash. Two stored rows that resolve to the same semantic update definition may therefore
reuse the same platform update process.

For `account_holdings`, changes in the latest holdings membership do not mint a new updater
identity. The stable account scope remains the same, while asset-indexed update statistics allow
newly held assets to backfill and previously held assets to remain in the shared historical bars
table.

The Account UID remains outside the update hash when `asset_source` is `assets` or `universe`,
because in those cases it selects credentials rather than the asset scope. For
`account_holdings`, the same Account UID is part of the hash because it defines the asset source.

## Resolver Boundary

Resolution is split into a pure review stage and an execution stage.

The pure stage:

1. loads the enabled stored configuration;
2. validates its account and asset-source invariants;
3. resolves the asset source;
4. resolves `(frequency_id, feed, adjustment)` through the storage registry;
5. confirms that the selected storage class is migrated and bound to one `TimeIndexMetaTable`;
6. builds the runtime configuration and returns a reviewable plan.

The pure stage does not resolve Secret values and does not contact Alpaca.

The execution stage:

1. repeats resolution against a single evaluation time;
2. resolves the configured Account's stored Main Sequence Secret names;
3. constructs the Alpaca historical-data client;
4. constructs `AlpacaStockBarsNode` with the resolved output storage class;
5. runs the node.

The resolution response may show the derived MetaTable UID, logical identifier, physical table,
selected holdings-set UID and timestamp, and current resolved asset count. Those values are
read-only diagnostics, not configuration inputs.

## API And CLI Consequences

The public write surface becomes configuration-oriented:

```text
GET    /v1/market-data/bar-configurations
POST   /v1/market-data/bar-configurations
GET    /v1/market-data/bar-configurations/{configuration_uid}
PATCH  /v1/market-data/bar-configurations/{configuration_uid}
DELETE /v1/market-data/bar-configurations/{configuration_uid}
POST   /v1/market-data/bar-configurations/{configuration_uid}/actions/resolve
POST   /v1/market-data/bar-configurations/{configuration_uid}/actions/update
```

The update action accepts only the configuration UID from the path. It does not accept
`dataset_uid`, bars-profile fields, another Account UID, another asset scope, a hash namespace, or
an execution-mode override.

The equivalent CLI execution contract is:

```bash
alpaca-connectors market-data update \
  --configuration-uid <CONFIGURATION_UID> \
  --execute
```

Any retained single-asset shorthand must resolve a stored configuration. It may not bypass the
stored-configuration lifecycle by constructing and running an ephemeral configuration.

## Storage And Migration Consequences

The new configuration tables are row-oriented project MetaTables and belong in the existing
`src.migrations:migration` provider. Their schema must be introduced through a new revision after
the current migration head. Existing migration files must not be edited.

The existing Alpaca bars `TimeIndexMetaTable` storage contracts do not change. Adding another
supported `(frequency_id, feed, adjustment)` still requires a dedicated storage class and a
migration before a stored configuration can resolve to it.

No account-holdings configuration can be globally seeded because valid Account UIDs are specific
to the active Main Sequence environment.

## Consequences

Positive:

- users can maintain and review several reusable bars configurations;
- users never provide output MetaTable UIDs;
- account holdings become a reusable dynamic asset source without duplicating an asset list;
- stale holdings fail explicitly instead of silently producing misleading coverage;
- configuration review is side-effect free and does not expose Secret values;
- all API, CLI, and scheduled execution paths share one resolver.

Tradeoffs:

- account-holdings bars updates depend on a separate, recent holdings-capture workflow;
- a configuration can become temporarily non-runnable when its latest holdings snapshot ages
  beyond 30 days;
- cash must be excluded explicitly before Alpaca stock-symbol resolution;
- configuration CRUD spans a configuration row and, for explicit assets, membership rows;
- changing a configuration's bars profile or stable scope identity may select a different output
  table or platform update process, while previously written bars remain intact.

## Required Validation

Implementation is complete only when tests prove that:

- all three asset sources enforce mutually exclusive stored shapes;
- account-holdings resolution selects the newest eligible `AccountHoldingsSet` and only its rows;
- a snapshot exactly 30 days old is eligible and an older snapshot is rejected;
- cash is excluded and unresolved non-cash holdings block execution;
- account-holdings resolution performs no Alpaca call and no holdings capture;
- the resolver maps every accepted bars profile to exactly one migrated output MetaTable;
- no update API or CLI accepts `dataset_uid`;
- preflight does not resolve Secret values;
- execution accepts a stored configuration UID and uses the same resolution logic as preflight;
- dynamic universe and account-holdings membership do not change updater identity solely because
  membership changed;
- deleting a configuration does not delete bars observations or update-process history.

## Job execution contract

The platform execution surface is one generic, on-demand Main Sequence Job named
`Alpaca Bars Update`. A JobRun accepts only `--configuration-uid <UUID>`. The launcher resolves the
durable row when the run starts and delegates to `execute_market_data_update`. With Main Sequence
SDK 8.1, `TimeIndexTableUpdater.run()` executes one update cycle directly and no longer accepts a
separate force switch.

The FastAPI update action performs the read-only resolver preflight and then submits this Job. It
returns `202 Accepted` with the JobRun UID instead of running the producer inside the request
process. JobRun state is exposed under the Operations API with no-store caching and sanitized
failure output. Dataset, account, source, bar profile, Secret, hash, and legacy run-switch overrides
are not part of the Job contract.

The Job has no default schedule because a schedule cannot currently provide the per-run
configuration argument. The previous environment-variable scheduled launcher remains only until
the new Job has completed its live cutover proof; remote retirement is an explicit platform
operation, not a consequence of deleting a YAML file.
