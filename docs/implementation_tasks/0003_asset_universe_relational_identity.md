# 0003 - AssetUniverse Relational Identity

> **Status:** implemented and applied on 2026-09-03.
> **Data policy:** create the new schema without legacy backfill. Existing connector-managed
> universes and universe-scoped bar configurations may be removed manually before the revision is
> applied, as already agreed for this project.

## Problem

The current implementation incorrectly uses an ms-markets `AssetCategory` row as the identity of a
registered universe. It stores the extraction `source_uid` and connector lifecycle state inside
`AssetCategory.metadata_json`. This makes a category look like configuration, provides no database
foreign key from the registered universe to the category it materializes, and causes
`universe_uid` in bar configurations to actually mean `AssetCategory.uid`.

An Asset Universe and an Asset Category are different resources:

- `AssetUniverse` is the connector-owned registration and lifecycle resource.
- `UniverseSource` is the explicit extraction configuration selected by that registration.
- `AssetCategory` is the ms-markets container into which a successful Run writes memberships.

No source, provider, category, or credential is inferred. Creating a universe always requires the
explicit source configuration supplied by the operator.

## Target relationship

```text
UniverseSourceTable
  uid PK
     ^
     | AssetUniverseTable.source_uid FK RESTRICT, UNIQUE, NOT NULL
     |
AssetUniverseTable
  uid PK                         registered Universe UID
  source_uid FK                 extraction configuration
  asset_category_uid FK         materialization target
  is_active
  created_at / updated_at
     |
     | asset_category_uid FK RESTRICT, UNIQUE, NOT NULL
     v
AssetCategoryTable
  uid PK                         category UID, not Universe UID
     ^
     | AssetCategoryMembership.category_uid FK
AssetCategoryMembershipTable

AlpacaBarsConfigurationTable.universe_uid
     |
     +--------------------------> AssetUniverseTable.uid FK RESTRICT
```

`source_uid` is unique because one explicit source configuration owns one registered universe.
`asset_category_uid` is unique because one materialized category belongs to one registered
universe. `AssetCategory.metadata_json` must not store `source_uid` or `is_active` after this
refactor.

The API and UI expose `AssetUniverse.uid` simply as `uid`. They expose the linked category as
`asset_category_uid`; they never label the category UID as the universe UID. The internal
`HOLDINGS__<SYMBOL>` category identifier can remain an ms-markets category business key, but it is
not presented as the registered universe's identity.

## Persistence contract

Add a project-owned, row-oriented `AssetUniverseTable` to the existing
`src.migrations:migration` provider.

| Column | Contract |
| --- | --- |
| `uid` | UUID primary key generated once for the registered universe |
| `source_uid` | required unique FK to `UniverseSourceTable.uid`, `ON DELETE RESTRICT` |
| `asset_category_uid` | required unique FK to `AssetCategoryTable.uid`, `ON DELETE RESTRICT` |
| `is_active` | required boolean controlling Run and downstream use |
| `created_at` | required UTC creation timestamp |
| `updated_at` | required UTC timestamp of the latest lifecycle change |

Use the repository-prefixed physical table name generated for the
`asset_universe` concept. Give every column complete label and description metadata. Add indexes
for `source_uid`, `asset_category_uid`, and `is_active`; the two FK columns also have unique
constraints.

`UniverseSourceTable` and the new table are provider-owned MetaTables. `AssetCategoryTable` remains
owned by ms-markets and is included only in the SQLAlchemy metadata dependency closure. The real
foreign keys are authored in SQLAlchemy and applied by Alembic; they are not encoded as JSON or
invented as MetaTable UID mappings.

## Required behavior changes

### 1. Registration

`POST /v1/universes` remains the explicit registration path and keeps its required `name`,
`symbol`, and `source_url` input.

The service must:

1. validate the explicit source values;
2. create or resolve the exact `UniverseSource` selected by those values;
3. create an empty `AssetCategory` with the internal `HOLDINGS__<SYMBOL>` category key;
4. create an `AssetUniverse` row containing both real foreign keys;
5. return the new Universe UID plus `source_uid` and `asset_category_uid`.

Creation performs no holdings extraction. A failed multi-row registration must compensate only
for rows created by that request so it cannot leave a source, category, or universe orphan.

### 2. Read and lifecycle actions

List, detail, update, activate, deactivate, Run, and delete accept `AssetUniverse.uid`.
Read responses join the linked source and category to return the name, symbol, source URL, category
UID, asset count, and lifecycle state.

Run resolves both links from the selected universe row, extracts from the linked source, and
replaces memberships only in the linked category. It must never search for a category by ticker or
read connector configuration from `AssetCategory.metadata_json`.

Delete is blocked while a bar configuration references the universe. Otherwise it deletes in this
order:

1. category memberships;
2. the `AssetUniverse` row, releasing its category FK;
3. the linked `AssetCategory`.

The `UniverseSource` remains a separately managed configuration and is not deleted implicitly.

### 3. Blocker reporting

Preserve the corrected Run preflight contract: top-level `blockers` must name every missing and
ambiguous symbol. The result payload also retains the structured
`missing_registered_symbols` and `ambiguous_registered_symbols` arrays. A generic
"unresolved asset-registration blockers" message is only a fallback for an unknown future blocker
type.

### 4. Bar configurations

Change `AlpacaBarsConfigurationTable.universe_uid` so it references
`AssetUniverseTable.uid`, not `AssetCategoryTable.uid`. Configuration validation loads the active
AssetUniverse and Run resolution follows `asset_category_uid` to read category membership.

The API field may remain named `universe_uid`, but its meaning becomes unambiguous: it always holds
the registered Asset Universe UID. Resolution diagnostics should report both `universe_uid` and
`asset_category_uid`.

### 5. API, CLI, and static site

- Replace internal `MaterializedUniverse*` response/request names with `AssetUniverse*` names.
- Keep canonical routes under `/v1/universes`; do not add compatibility inference routes.
- Make the registered-universe table use `AssetUniverse.uid` as its row key.
- Show `asset_category_uid` only where materialization diagnostics are useful.
- Enable Run based on `is_active` and the linked source/category, not metadata JSON.
- Make registered-universe CLI commands accept a Universe UID. Keep `universe-source` CRUD for the
  separate extraction configuration; it must not create or infer a registered universe.
- Update Command Center TypeScript types and resource tests so the UI cannot confuse a category UID
  with a Universe UID.

## Schema revision and reset procedure

Create a new Alembic revision after current head `0005`; do not edit any applied revision.
The revision must:

1. create `alpaca_connectors__asset_universe` with both real FKs and uniqueness constraints;
2. replace the bar-configuration `universe_uid` FK target with the new table;
3. leave core ms-markets category tables under the ms-markets migration provider;
4. contain no legacy category-to-universe backfill.

Before applying the revision, explicitly remove existing universe-scoped bar configurations and
current connector-managed universes through their supported delete paths. Other bar configurations
can remain because their `universe_uid` is null. Verify the precondition before DDL so an overlooked
legacy reference fails clearly rather than being silently reinterpreted.

Use only the provider lifecycle:

```bash
mainsequence migrations current --provider src.migrations:migration
mainsequence migrations revision --provider src.migrations:migration -m "add asset universe identity"
mainsequence migrations upgrade --provider src.migrations:migration head
```

## Implementation sequence

1. Add `AssetUniverseTable`, its typed row model, and CRUD queries under `src/universes/`; add it to
   the project migration provider in parent-before-child dependency order.
2. Generate and review the new Alembic revision, including the changed bar-configuration FK.
3. Refactor universe registration/read/update/delete services around Universe UID and the two FKs.
4. Refactor Run planning/execution to follow the stored links and preserve exact blocker symbols.
5. Refactor bar-configuration validation and market-data resolution to follow
   `AssetUniverse.asset_category_uid`.
6. Update FastAPI schemas/discovery, CLI argument names, static-site types, and Command Center tests.
7. Update the holdings-universe ADR, repository instructions, API docs, and operator docs to use the
   corrected identity model.
8. Perform the agreed manual data reset, apply the migration, recreate one IVV universe, and verify
   the live workflow.

## Acceptance checks

The refactor is complete only when all of these are true:

- SQLAlchemy metadata and database introspection show both required `AssetUniverse` foreign keys.
- The two linked UIDs are non-null and unique.
- A created Universe UID is distinct from its category UID.
- No connector-owned `source_uid` or active flag is written to category metadata.
- Run with a Universe UID reads exactly its linked source and writes exactly its linked category.
- Run preflight shows the exact missing/ambiguous symbols in the Command Center dialog.
- An inactive universe cannot Run or feed a bars configuration.
- A universe referenced by a bars configuration cannot be deleted, and the blocker names the
  dependent configuration.
- A source or category referenced by an AssetUniverse cannot be deleted directly.
- A universe-scoped bars configuration stores the Universe UID and resolves membership through the
  linked category.
- Delete removes memberships, universe, and category without deleting the source or any Asset.
- API, CLI, backend unit tests, static-site interaction tests, Ruff, and the full test suite pass.
- `mainsequence migrations current --provider src.migrations:migration` reports the new head and
  all provider MetaTables finalize as active.
- A live IVV recreation returns three distinct identities with the expected links:
  `universe.uid`, `universe.source_uid`, and `universe.asset_category_uid`.

## Not claimed by this plan

- No legacy metadata-to-FK data backfill.
- No provider or URL inference.
- No relaxation of strict Main Sequence asset resolution for category membership.
- No change to the fact that OpenFIGI enrichment is optional during Alpaca asset registration.

## Verification record

- Alembic provider current: `0006 (head)`.
- Database introspection confirms `AssetUniverse.source_uid` references
  `UniverseSource.uid`, `AssetUniverse.asset_category_uid` references `AssetCategory.uid`, and
  `AlpacaBarsConfiguration.universe_uid` references `AssetUniverse.uid`, all with
  `ON DELETE RESTRICT`.
- The single legacy `HOLDINGS__IVV` category had zero memberships and zero referencing bar
  configurations; it was deleted before the new registration was created. Its source was retained.
- Fresh IVV registration created Universe `ea3a710c-f166-4e4c-b145-8ec7e85b6b6c`, Source
  `28f98530-9380-4a3b-b70e-3da8b972c205`, and Asset Category
  `cc040e85-092d-4943-8424-7fc2330b0770` as distinct identities.
- The created category has `metadata_json = null` and zero memberships.
- Live Run preflight followed the registered links, found the one registered IVV constituent
  (`NVDA`), and named all 503 missing symbols in the top-level blocker message.
- No change to the ms-markets ownership of `AssetCategory` and `AssetCategoryMembership`.
