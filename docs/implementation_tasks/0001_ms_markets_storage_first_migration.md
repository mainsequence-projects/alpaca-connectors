# 0001 - ms-markets Storage-First Migration

> **Status:** Phases 1–8 implemented and import/unit-test green (69 passed, 0 new failures);
> Phase 9 (narrative docs + agent-card) and all `[needs backend]` live-verification steps remain.
> **Target runtime:** `mainsequence==4.3.14`, `ms-markets==0.0.44` (both verified installed in `.venv`).
> **Revised:** 2026-06-08 — reconciled against a deep dependency review + adversarial
> verification of every API claim against the installed packages. Checkbox tasks are the
> source of truth for execution.

## Goal

Migrate the Alpaca connector project from the old Main Sequence `tdag` / implicit DataNode
registration architecture to the current **storage-first** architecture while preserving the
existing business logic **exactly**:

- strict Alpaca US equity discovery and FIGI-backed registration
- ETF holdings extraction and reusable `HOLDINGS__<ETF>` categories
- Alpaca OHLCV stock-bar publishing for explicit tickers or holdings universes
- thin CLI, FastAPI, scheduled-job, and Command Center surfaces

The dependency direction for market-domain behavior becomes:

```text
alpaca-connectors -> ms-markets (msm) -> mainsequence
```

Project business logic prefers `msm` / `ms-markets` market primitives; raw `mainsequence`
is used only for platform/CLI/migration plumbing and the secrets client.

## Why this migration is mandatory (verified runtime facts)

1. `import mainsequence.tdag` → **`ModuleNotFoundError`**. The whole time-series base
   (`DataNode`, `DataNodeConfiguration`, `DataNodeMetaData`, `RecordDefinition`) is gone.
   `src/data_nodes/alpaca_bars.py:9` fails at import today.
2. `import mainsequence.dashboards` → **`ModuleNotFoundError`**. No `dashboards`/`streamlit`
   scaffold ships. `dashboards/sample_app/app.py:9` is dead.
3. `mainsequence.client.models_tdag` is **gone**. `api/app/services.py:279` fails at import.
4. Asset/markets concepts left `mainsequence.client`. The `AssetTable` columns are now exactly
   `('uid', 'unique_identifier', 'asset_type')` — **no integer `id`, no `ticker`, no `figi`,
   no `current_snapshot`**. Provider symbols live in `OpenFigiDetails` keyed by `asset_uid`.

## Architecture: before -> after (every row verified against the installed packages)

| Concern | OLD (removed) | NEW (target) |
|---|---|---|
| Node base | `from mainsequence.tdag import DataNode` | `from msm.data_nodes.assets import AssetIndexedDataNode` |
| Config base | `mainsequence.tdag.DataNodeConfiguration` | `msm.data_nodes.assets.AssetIndexedDataNodeConfiguration` (inherits `asset_list`; `offset_start` comes from core `DataNodeConfiguration`) |
| Node identity / description | `DataNodeMetaData(identifier=, description=)` mutated onto `config.node_metadata` | derived from the storage class `__metatable_identifier__` / `__metatable_description__` via `_required_storage_table()` |
| Output schema | `records: list[RecordDefinition]` on the config | SQLAlchemy `mapped_column`s on a `PlatformTimeIndexMetaTable` storage class |
| Storage bases | (implicit) | `from msm.base import MarketsBase, MarketsTimeIndexMetaTableMixin`; `from mainsequence.meta_tables import PlatformTimeIndexMetaTable` |
| Asset index dimension | hardcoded `unique_identifier` | `from msm.settings import ASSET_IDENTIFIER_DIMENSION` (`== "asset_identifier"`) |
| Asset row | `mainsequence.client as msc` → `msc.Asset` | `from msm.api.assets import Asset` |
| Asset category | `msc.AssetCategory` | `from msm.api.assets import AssetCategory` |
| Provider ticker / figi | `asset.ticker`, `asset.figi`, `asset.current_snapshot.ticker` | `from msm.api.assets import OpenFigiDetails` (`.figi`, `.ticker`, `.name`), joined by `asset_uid` |
| Asset identity key | integer `asset.id` | UUID `asset.uid` |
| Asset lookup | `msc.Asset.filter(...)`, `msc.Asset.query(figi__in=...)` | `Asset.get_by_unique_identifier(uid)`, `Asset.get_by_uid(uid)`, `Asset.filter(unique_identifier=, unique_identifier_contains=, asset_type=)` — **no `__in` / no batch**, value `None`/`""` is skipped |
| Category get-or-create | `msc.AssetCategory.get_or_create(...)` | `AssetCategory.get_by_unique_identifier(uid)` + `AssetCategory.upsert(...)` |
| Category set members | `category.assets` + `remove_assets` + `append_assets` | `AssetCategory.replace_memberships(category_uid=, asset_uids=[...])` (atomic full replace) |
| Category list members | `category.assets` attribute | `msm.repositories.asset_categories.search_asset_category_memberships(context, category_uid=)` |
| FIGI registration | `msc.Asset.register_asset_from_figi(figi=, timeout=)` | `msm.services.assets.openfigi.query_by_figi(figi)` → `build_asset_rows_from_openfigi_result(item, asset_uid=, time_index=)` → upsert `Asset` / `OpenFigiDetails` / `AssetSnapshot` |
| Secrets | `msc.Secret.get_or_none(name=)` | `from mainsequence.client import Secret; Secret.get_or_none(name=)` — **`get_or_none` still exists**, missing→`None` contract preserved |
| Markets constants | `msc.MARKETS_CONSTANTS.FIGI_*` | **GONE** — replace with local string literals (see Task 1) |
| Read stored bars by node id | `mainsequence.client.models_tdag.DataNodeStorage.get_data_between_dates_from_node_identifier(... unique_identifier_list=...)` → `DataFrame` | `from mainsequence.client import TimeIndexMetaTable; TimeIndexMetaTable.get_data_between_dates_from_node_identifier(node_identifier=, start_date=, end_date=, dimension_filters={"asset_identifier":[uid]}, columns=[...])` → **`(DataFrame, TimeIndexMetaTable)` tuple; SDK builds `pd.DataFrame(all_results)`, so `time_index` is a column on read** |
| Streamlit scaffold | `mainsequence.dashboards.streamlit.scaffold.{PageConfig, run_page}` | **no replacement** — plain `import streamlit as st; st.set_page_config(...)` |
| `mainsequence` logger | `from mainsequence import logger` | unchanged |
| Process bootstrap | implicit on import | `import msm; msm.start_engine(models=[...])` once per process with backend SQLAlchemy table/storage classes — **NEW, REQUIRED** |
| Schema creation | implicit at first `node.run()` | `mainsequence migrations upgrade --provider <p> head` — **NEW, REQUIRED** |

`msm.start_engine` signature (verified): `start_engine(*, management_mode='platform_managed', namespace=None, models=None, timeout=None) -> MarketsRuntime`.
Project code must pass backend SQLAlchemy table/storage classes in `models=[...]`, not typed row
API classes. Example: use `AssetTypeTable`, `AssetTable`, `OpenFigiAssetDetailsTable`,
`AssetSnapshotsStorage`, and project-local Alpaca storage classes; do not pass `AssetType`,
`Asset`, or `OpenFigiDetails`.

## Key architecture decisions (baked into the tasks below)

These are the **behavior-preserving** choices. They resolve the blocking open questions from
the review. Deviate only with an explicit reason.

- **D1 — Table granularity = one storage class per `(frequency_id, feed, adjustment)` triple,
  generated by a factory.** This mirrors the old "one physical table per triple" behavior and
  avoids the *critical* key-collision risk of funnelling every triple into a single
  `(time_index, asset_identifier)` table. `__index_names__` stays `["time_index",
  "asset_identifier"]` so the flat per-asset range-map path is valid. Each storage class declares
  explicit table names with the triple in the concept segment (for example `bars_1d_iex_raw`),
  `__cadence__ = "1d"`, and `__metatable_extra_hash_components__` for only the non-cadence variant
  fields (`feed`, `adjustment`) so identical OHLCV schemas do not collapse to one storage identity.
- **D2 — Preserve the legacy DataNode identifier.** The factory sets
  `__metatable_identifier__ = f"alpaca_stock_bars_{frequency_id}_{feed}_{adjustment}"` so the
  registered DataNode identifier resolves to the **exact** legacy string
  (`alpaca_stock_bars_1d_sip_all`). This keeps the FastAPI chart endpoint and Command Center
  workspace contract working **unchanged** (D2 must be re-verified against the actual
  registered identifier post-migration, which can be namespace-prefixed — see R-1).
- **D3 — Initial registered matrix = the production set only.** Register `1d/sip/all` (used by
  `scheduled_jobs.yaml` + the chart default) and any combo referenced by existing docs/tests;
  add more storage classes intentionally later.
- **D4 — Keep request grouping.** `get_asset_update_range_map_great_or_equal` returns a *flat*
  `{uid: {start_date, ...}}` map; it does **not** bucket. Re-derive groups (uids sharing the
  same `start_date`) so each Alpaca request still uses one `request_start` across its symbol
  batch — identical to today's `group_bindings_by_last_update`.
- **D5 — Asset identity is UUID end-to-end.** All public result dicts that carried integer
  `.id` become `.uid` (UUID). Every `json.dumps` of these values uses `default=str`.
- **D6 — `asset_category_unique_identifier` is a plain hashed config field.** Drop
  `json_schema_extra={"update_only": True}` (forbidden by the ms-markets skills). (If we ever
  need it non-hash-affecting, `json_schema_extra={"hash_excluded": True}` is the supported
  mechanism — not `update_only`.)
- **D7 — Keep the project-local OpenFIGI HTTP client.** Preserves the anonymous-tier
  (chunk=10, env-only key, missing-key allowed) behavior. Do **not** adopt
  `msm.services.assets.openfigi.query_figi` for the bulk path (it requires a secret-named key
  and raises on missing). The `msm` openfigi helpers are used only for the typed-row builders.
- **D8 — FIGI is the canonical public-equity asset identity.** A public equity such as Apple is
  registered as `Asset.unique_identifier = "BBG000B9XRY4"`; ticker is stored only as provider /
  display metadata such as `OpenFigiDetails.ticker = "AAPL"` and `AssetSnapshot.ticker = "AAPL"`.
  Every ticker-driven workflow must resolve ticker input through OpenFIGI details or snapshots
  before using the asset UID / FIGI identity internally.

## Business logic to preserve (must not be rewritten)

- Alpaca symbol availability lookup + alias normalization.
- Strict ticker expansion from ETF holdings providers.
- Strict OpenFIGI pass ordering: common stock → ETP → REIT (first-match wins).
- Refusal to register symbols missing from Alpaca or FIGI; no custom-asset fallback.
- Holdings-category blockers for missing/ambiguous registered assets.
- Stock-bar fetch, 200-symbol batching, incomplete-bar filtering, daily 16:00 New York
  session-close timestamp, `datetime64[ns, UTC]` normalization, concat + dedup keep-last.
- CLI split between dry-run planning and `--execute` / `node.run()`.
- Shorthand `alpaca-connectors asset <ticker> update_prices <period>`.
- Scheduled IVV daily-bars launcher and the 4-step ETF maintenance ordering.

---

## Implementation Tasks

> Effort key: **S** ≤ ~½ day, **M** ~1 day, **L** multi-day. Phases are ordered; `(∥)` = can run
> in parallel. Verification that needs a live platform backend is marked **[needs backend]**;
> in this environment only imports + mocked unit tests + dry-runs are runnable.

### Implementation status (2026-06-08)

All code for Phases 1–8 is **implemented and import/unit-test green**. The project imported nowhere
before (it died on `mainsequence.tdag`); it now imports end-to-end on `mainsequence==4.3.14` +
`ms-markets==0.0.44`.

- **Test suite:** `69 passed, 2 failed`. The migration introduces **zero** new failures.
  The 2 failures (`tests/test_agent_artifacts.py`) are **pre-existing** — they fail on a clean
  checkout of `HEAD` too (`.agents/agent_card.json` is stale: name "Alpaca Connection Manager" /
  version 0.1.17 vs the test's hardcoded `alpaca-connectors-153` / pyproject 0.1.19, and the
  freshly SDK-copied `ms_markets`/`ms-markets` skill folders aren't listed). This is agent-card
  scaffolding drift, unrelated to the ms-markets storage migration (see Phase 9 / spawned task).
- **Two pre-existing latent bugs fixed in passing** (were masked by the total import failure):
  `api/app/schemas.py` and `api/app/services.py` imported `SUPPORTED_COMPONENT_PROVIDERS` /
  `ETF_PROVIDER_MAP_NORMALIZED` / `ETFS_MAIN_TICKERS` / `MAG_7_CATEGORY_SYMBOLS` from
  `src.settings`, where they never existed — repointed to `etf_extraction.settings`.
- **[needs backend] before release** (cannot run without an authenticated platform + DB):
  `mainsequence migrations upgrade --provider markets_migrations:migration head`;
  `msm.start_engine` attach; live `node.run()` and namespaced smoke run; live registration /
  category writes / chart read.

### Phase 1 — Settings, secrets & dependencies (PR-1, S) — unblocks everything ✅ DONE

- [x] `pyproject.toml`: pin `mainsequence>=4.3.14` and keep `ms-markets>=0.0.44` (listed first).
- [x] `requirements.txt`: regenerated via `uv lock` + `uv export` → `mainsequence==4.3.14`.
- [x] `src/settings.py`: secret resolution left as-is — `mainsequence.client.Secret.get_or_none`
      still exists and works (verified), missing→`None` contract preserved, no change needed.
- [x] `src/settings.py`: deleted `get_markets_constants()` + `MARKETS_CONSTANTS`; added local
      literals `FIGI_MARKET_SECTOR_EQUITY="Equity"`, `FIGI_SECURITY_TYPE_COMMON_STOCK="Common
      Stock"`, `FIGI_SECURITY_TYPE_ETP="ETP"`, `FIGI_SECURITY_TYPE_REIT="REIT"`; `get_figi_*`
      function names kept (consumed by `alpaca_us_equities.py`). These literals are OpenFIGI
      response metadata filters/classifiers, not legacy SDK compatibility constants.
- [x] `src/settings.py`: `get_openfigi_api_key()` kept env-only — no change.
- [x] `etf_extraction/settings.py`: no SDK touchpoints (pure scraping config) — no change needed.
- [x] Verify: `tests/test_settings.py` → **5 passed**; `uv lock` resolved 87 packages.

### Phase 2 — Storage classes + migration provider (PR-2, M) — unblocks node & reads ✅ CODE DONE

Done: `src/markets_storage/alpaca_bars.py` (`AlpacaStockBars1dSipAllStorage` and
`AlpacaStockBars1dIexRawStorage`, identifiers preserved as `alpaca_stock_bars_1d_sip_all` /
`alpaca_stock_bars_1d_iex_raw`, explicit table names (`bars_1d_sip_all` / `bars_1d_iex_raw`),
non-cadence storage hash components, `__cadence__ = "1d"`, registry + fail-loud `storage_for`),
`src/runtime.py` (`start_markets_engine`), and the SDK-scaffolded
`markets_migrations/` provider (loads as
`markets_migrations:migration`). `tests/test_markets_storage.py` → 7 passed. Live `migrations
revision`/`upgrade` and `start_engine` attach are **[needs backend]**.

- [x] New package `src/markets_storage/alpaca_bars.py`: one explicit storage class per registered
      triple (D3 = start with `1d/sip/all` and `1d/iex/raw`) + `storage_for()` registry +
      `project_storage_models()` + `METADATA`. (Factory deferred; explicit classes are safest and
      fully verifiable.)
- [x] Registry `ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE` + `storage_for(...)` raising for unknown.
- [x] `markets_migrations/` provider via `mainsequence migrations scaffold` (uses
      `build_metatable_migration_provider` + `METADATA` + `project_storage_models()`); added to
      `pyproject` packages.
- [x] `src/runtime.py` `start_markets_engine()` wrapping `msm.start_engine(models=[...])` with
      backend table/storage classes: `AssetTypeTable`, `AssetTable`,
      `OpenFigiAssetDetailsTable`, `AssetSnapshotsStorage`, `AssetCategoryTable`,
      `AssetCategoryMembershipTable`, and `project_storage_models()`.
- [x] Verify: storage maps (identifier/index/columns/FK), provider loads via SDK loader, runtime
      model list resolves. **[needs backend]** `mainsequence migrations upgrade ... head`.

### Phase 3 — Rebuild `AlpacaStockBarsNode` storage-first (PR-3, L) ✅ CODE DONE

Done: node rewritten onto `AssetIndexedDataNode` (storage-bound via `storage_for`, no
`DataNodeMetaData`/`records`); `get_asset_list()` returns unique-identifier strings; `update()`
uses the base flat range map + **kept** `group_bindings_by_last_update` (D4); support module
renamed the 2nd index level to `asset_identifier`, added `AssetTickerFigi`, dropped
`update_statistics_to_last_update_map`/`_chunked`. New `src/assets/resolution.py` centralizes the
`OpenFigiDetails` join (reused by Phases 5–7). `tests/test_alpaca_bars_support.py` updated → 4
passed. Node construction/`run()` is **[needs backend]** (the `storage_table` setter requires a
migrated+registered+attached table).

- [x] `AlpacaStockBarsConfig(AssetIndexedDataNodeConfiguration)`: hashed `frequency_id`/`feed`/
      `adjustment` (field validators normalize), inherited `asset_list`, plain hashed
      `asset_category_unique_identifier`, no `records`/`update_only`.
- [x] `AlpacaStockBarsNode(AssetIndexedDataNode)`: per-triple `storage_table` resolved in
      `__init__`; `_required_storage_table()` default for class-level identifier; no
      `DataNodeMetaData`.
- [x] `get_asset_list()` → unique-identifier strings (category branch via
      `asset_unique_identifiers_for_category`).
- [x] `update()` → `get_asset_update_range_map_great_or_equal()` flat map → kept grouping/batching.
- [x] `alpaca_bars_support.py`: `asset_identifier` rename, `OpenFigiDetails`-fed bindings via
      `AssetTickerFigi`, kept `group_bindings_by_last_update` (re-pointed), deleted dead helpers.
- [x] `src/assets/resolution.py` shared `OpenFigiDetails` resolution helpers.
- [x] Verify: imports, validator normalization, `tests/test_alpaca_bars_support.py` (4 passed).
      **[needs backend]** node construction + `run()`.

### Phase 4 — Asset registration via ms-markets (PR-4, L) ✅ CODE DONE

Done: `alpaca_us_equities.py` now looks up existing assets per-FIGI via
`Asset.get_by_unique_identifier`, registers via `Asset.upsert` + `OpenFigiDetails.upsert` built
from the already-classified `OpenFigiMatch` (no re-query), and returns uid strings; `cli/asset.py`
JSON output uses those uid strings. `AssetType` needs no pre-registration (no FK).
`tests/test_alpaca_us_equities.py` → 3 passed.

- [x] `src/assets/alpaca_us_equities.py`: replace `msc.Asset.query(figi__in=...)` with a
      per-FIGI loop `Asset.get_by_unique_identifier(figi)` (FIGI *is* the equity
      `unique_identifier`). Preserve `sorted(set(...))` dedup. (R-2: N round-trips — accepted.)
- [x] Replace `msc.Asset.register_asset_from_figi(...)` with the compose path:
      `query_by_figi(figi)` → allocate `asset_uid` + `time_index` →
      `build_asset_rows_from_openfigi_result(...)` → upsert `Asset` (+`AssetType` "equity" if
      required, OQ-4) + `OpenFigiDetails` + `AssetSnapshot`. Keep dry-run/execute semantics and
      idempotency.
- [x] `.id` → `.uid` (UUID) at `:577`, `:583`, `:623-625`; change the result dataclass fields
      `existing_assets_by_symbol` / `existing_assets_by_figi` (`:229-230`) to UUID-typed (D5).
- [x] Keep verbatim: `_build_symbol_alias_candidates`, `_select_openfigi_candidate`,
      `build_default_classification_passes`, the common_stock→etp→reit ordering,
      `warnings_by_symbol`, `fetch_alpaca_us_equities`, output dict keys, injection seams.
- [x] `src/cli/asset.py`: `getattr(asset, "id", asset)` / `asset.id` (`:148`, `:153`) → use
      `str(asset.uid)` (or `json.dumps(..., default=str)`) so UUIDs serialize (D5).
- [x] Verify: `tests/test_alpaca_us_equities.py` (mock `Asset.get_by_unique_identifier`,
      `query_by_figi`, `build_asset_rows_from_openfigi_result`; assert UUID, idempotency,
      common/etp/reit/missing-figi/missing-alpaca/already-registered cases).

### Phase 5 — Holdings categories via ms-markets universes (PR-5, M) ✅ CODE DONE

Done: `holdings_categories.py` uses `AssetCategory.upsert` (get-or-create) +
`replace_memberships` (atomic), resolves component tickers via `OpenFigiDetails`
(`assets_for_ticker`), and carries uid strings (kept the `asset_ids` field names — dataclasses,
JSON-safe). `etf_extraction/tests/test_holdings_categories.py` → 4 passed.

- [x] `etf_extraction/holdings_categories.py`: route the `_load_mainsequence_client()` seam to
      `msm` + `from msm.api.assets import Asset, AssetCategory`. Keep the lazy seam so the
      extractor tree stays SDK-free.
- [x] `msc.AssetCategory.get_or_create` → `AssetCategory.get_by_unique_identifier` +
      `AssetCategory.upsert(unique_identifier=, display_name=, description=)`. Preserve
      `display_name == unique_identifier == HOLDINGS__<TICKER>` and the description format.
- [x] Collapse `category.assets` + `remove_assets` + `append_assets` into one
      `AssetCategory.replace_memberships(category_uid=cat.uid, asset_uids=[...])`.
- [x] `msc.Asset.filter(current_snapshot__ticker__in=...)` → ticker resolution via
      `OpenFigiDetails.filter(ticker=...)` → `asset_uid` → `Asset.get_by_uid` (per-ticker; no
      batch). Preserve the present-unique / missing / ambiguous classification + sorting.
      (D8: equity ticker is *not* `Asset.unique_identifier`.)
- [x] `asset.id` / `_coerce_asset_id` → `.uid`; `AssetCategorySyncResult.asset_ids` and
      `HoldingsAssetCategoryPlan.existing_asset_ids_by_symbol` become UUID-typed (D5/OQ-10).
- [x] `src/cli/holdings_category.py`: `json.dumps` of `existing_asset_ids_by_symbol` /
      `asset_ids` (`:79-92`) → `default=str` (D5).
- [x] Keep verbatim: `build_holdings_asset_category_unique_identifier`,
      `infer_holdings_component_provider`, plan building, blocker/summary contract, injection
      seams. `src/holdings_categories.py` shim updated to match.
- [x] Verify: `etf_extraction/tests/test_holdings_categories.py` (full-replace semantics,
      UUID results, classification assertions).

### Phase 6 — Jobs, CLI & `start_engine` bootstrap (PR-6, M) ✅ CODE DONE

Done: `cli/bars.py` resolves tickers via `assets_for_ticker` (asset_list = uid strings), reports
`storage_table.__metatable_identifier__`, keeps the `node.run()` 2-tuple unpack;
`start_markets_engine()` wired into command handlers/builders that touch ms-markets
(`build_stock_bars_node`, `cli/asset.py`, `cli/holdings_category.py`) and the ETF maintenance
job `main()`; daily IVV job argv unchanged.
`tests/test_cli.py` + `tests/test_run_daily_stock_bars.py` → 13 passed.

Generic `bars run` default `1d/iex/raw` is storage-backed by
`AlpacaStockBars1dIexRawStorage`; shorthand `asset <ticker> update_prices daily` and chart
defaults continue to use `1d/sip/all` / `AlpacaStockBars1dSipAllStorage`.

- [x] `src/cli/bars.py`: replace `_load_mainsequence_client` +
      `msc.Asset.filter(current_snapshot__ticker__in=...)` with `OpenFigiDetails.filter(ticker=)`
      → `asset_uid` → `Asset.get_by_uid`. Preserve the distinct errors (not in Alpaca / not
      registered / ambiguous). Ticker-scoped `config["asset_list"]` becomes a list of
      unique_identifier strings. `node.run(debug_mode=True, force_update=...)` returns the
      2-tuple `(error_on_last_update, update_result)` — **keep the unpack** (verified). Replace
      `node.get_table_metadata().identifier` reads.
- [x] Add `start_markets_engine()` before each CLI/job path touches ms-markets: asset
      registration, holdings-category sync, stock-bars node construction, and both job scripts'
      `main()`. Never auto-bootstrap from imports; fail loud if runtime/migrations are missing.
- [x] `src/jobs/run_etf_maintenance_routines.py`: re-point `build_stock_bars_node` /
      `_run_stock_bars_node`; preserve the 4-step order, `has_blockers()` gate,
      `force_update=True`, dry-run/continue-on-error.
- [x] `src/jobs/run_daily_stock_bars_holdings_ivv.py` + `scheduled_jobs.yaml`: argv unchanged
      (`HOLDINGS__IVV 1d sip all`), only add the bootstrap.
- [x] Keep verbatim: `cli/main.py` dispatch, shorthand routing, period normalization, default
      divergence (`bars` `iex/raw` vs shorthand/ETF `sip/all`).
- [x] Verify: `tests/test_cli.py`, `tests/test_run_daily_stock_bars.py`.

### Phase 7 — API reads (PR-7, M) ✅ CODE DONE

Done: `services.py` reads via `TimeIndexMetaTable.get_data_between_dates_from_node_identifier`
(2-tuple, `dimension_filters={"asset_identifier":[uid]}`; installed SDK returns
`pd.DataFrame(all_results)`, so `time_index` is a column); asset search split across `Asset` +
`OpenFigiDetails` flattened into one view; FastAPI `startup`
bootstrap added. Chart node_identifier default `alpaca_stock_bars_1d_sip_all` still resolves (D2).
Fixed the latent `src.settings` import bugs. `tests/test_api_app.py` → 14 passed.

- [x] `api/app/services.py`: `from mainsequence.client.models_tdag import DataNodeStorage` →
      `from mainsequence.client import TimeIndexMetaTable`. Replace
      `DataNodeStorage.get_data_between_dates_from_node_identifier(... unique_identifier_list=[uid])`
      with `TimeIndexMetaTable.get_data_between_dates_from_node_identifier(node_identifier=,
      start_date=, end_date=, dimension_filters={"asset_identifier":[uid]},
      columns=["open","high","low","close","volume"])`. Keep the `bars_frame, _ = ...` unpack
      (still a 2-tuple). Installed SDK `_get_data_between_dates_common` returns
      `pd.DataFrame(all_results)` and does not call `set_index`, so `time_index` is consumed as a
      column.
- [x] `api/app/services.py`: `msc.Asset.filter(**filters)` (`:464-474`) → split across
      `Asset.filter(unique_identifier=, unique_identifier_contains=)` and
      `OpenFigiDetails.filter(ticker=, figi=, name_contains=)` joined by `asset_uid`. Django
      `__contains` → msm `_contains`. Adapt `_asset_search_text` to read from `OpenFigiDetails`.
      Preserve `_dedupe_assets` first-wins + the `resolve_*` exact-match taxonomy + ValueErrors.
- [x] `api/app/main.py`: add `start_markets_engine()` as a FastAPI startup event so both lazy
      sites assume an initialized runtime.
- [x] Confirm the chart `node_identifier` default `alpaca_stock_bars_1d_sip_all`
      (`schemas.py:172`, `main.py:181`) still resolves under D2 (re-verify the registered
      identifier post-migration; update schema/default in lockstep only if it differs).
- [x] Keep verbatim: the OHLC → lightweight-charts spec pipeline, response envelope, schema
      validators, CORS/routing, `command_center_models.py`.
- [x] Verify: `tests/test_api_app.py` (2-tuple read; asset-search split; **chart spec preserved
      byte-for-byte** — Command Center UI contract).

### Phase 8 — Dashboard (PR-8) ✅ CODE DONE

Done: `dashboards/sample_app/app.py` rewritten to plain `st.set_page_config(page_title="Main
Sequence Demo App", layout="wide")` (the removed `mainsequence.dashboards.streamlit.scaffold` has
no SDK replacement); `from mainsequence import logger` unchanged.

- [x] `dashboards/sample_app/app.py`: `from mainsequence.dashboards.streamlit.scaffold import
      PageConfig, run_page` has **no replacement**. Rewrite with plain `import streamlit as st;
      st.set_page_config(page_title="Main Sequence Demo App", layout="wide")` preserving the
      title/wide-layout intent. `from mainsequence import logger` is unchanged. (OQ-7: confirm
      no separate dashboards distribution exists before rewriting.)

### Phase 9 — Docs & agent-state reconciliation (PR-9, S) — ✅ DOCS DONE

- [x] Updated `docs/data_nodes/alpaca_bars.md`, `docs/assets/registration.md`,
      `docs/assets/holdings_categories.md`, `docs/operations/jobs.md`, `README.md`,
      `docs/index.md`, `docs/api.md` to describe storage-first, `asset_identifier`, the migration
      prerequisite, and `msm.start_engine`. (`development.md` / `command_center/app_component.md` /
      the extractor docs had no stale SDK references; the ADR is left as a historical record.)
- [ ] Reconcile `.agents/status.md`, `.agents/tasks.md`, `.agents/record.md` + `agent_card.json`
      with this checkout (project UID `2933f4f9-31e1-471c-87c6-51c1a2caacbc`, SDK `4.3.14`). Keep
      `AGENTS.md` scaffold markers intact. **Flagged as a separate task** (it fails
      `tests/test_agent_artifacts.py` on `HEAD` already — agent-scaffolding drift, not migration).

---

## Risk register

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| R-1 | **Table-identity / identifier resolution.** D2 assumes the registered identifier equals the legacy string, but namespacing can prefix it. | **Critical** | After migration, read the actual registered identifier; if prefixed, set the API `node_identifier` default in lockstep (Phase 7). |
| R-2 | No bulk asset lookup (`Asset.filter` has no `__in`, skips `None`/`""`). FIGI/ticker resolution is N round-trips. | High | Accept perf hit; isolate in helpers; revisit if a batch repo call appears (OQ-6). |
| R-3 | Provider ticker/figi gone from `AssetTable`; all binding reads must use `OpenFigiDetails` by `asset_uid`; missing detail rows → silent "unresolved". | High | Re-point reads; assert detail coverage in tests. |
| R-4 | Hash sensitivity: class import path + MRO-reflected `__init__` kwargs feed the hash; renaming/moving the node orphans tables. | High | Keep field names + class path stable; normalize via validators; create fresh tables via migration. |
| R-5 | UUID propagation: int `.id` consumers (`cli/asset.py`, `cli/holdings_category.py`, result dicts) break on `json.dumps`. | High | D5: `default=str`; enumerate every consumer (done in Tasks 4/5/6). |
| R-6 | Missing `start_engine` or migration → every entrypoint `RuntimeError` / missing table. | High | Bootstrap at all 4 entrypoints + migration gate; fail loud. |
| R-7 | Index-level rename `unique_identifier`→`asset_identifier` must be consistent or frame validation rejects at runtime. | Medium | Apply in `normalize_stock_bars_frame` + `update()`; unit-test. |
| R-8 | Classification literals must track actual OpenFIGI response values; treating removed SDK `MARKETS_CONSTANTS` as the source of truth would preserve the wrong dependency boundary. | Low | Source of truth is OpenFIGI response metadata. FIGI remains identity; `security_market_sector` drives AssetType; `security_type`/`security_type_2` only filter candidates. |
| R-9 | Live verification (migrations, `node.run()`) needs a platform backend unavailable in dev. | Medium | Verify imports + mocked unit tests + dry-runs here; run live steps in an authenticated env before release. |

## Resolved questions (were open in earlier drafts)

- **`node.run()` return shape** — confirmed returns `(error_on_last_update, update_result)`
  (`run_operations.py:788`, despite the `-> None` annotation). Keep the existing unpack.
- **`Secret.get_or_none`** — still exists; missing→`None` contract preserved without try/except.
- **Table granularity / identifier** — decided (D1/D2/D3): factory, one class per triple,
  legacy identifier preserved, start with the production matrix.
- **Request grouping** — decided (D4): keep grouping over the flat range map.
- **Public equity identity (was OQ-2)** — decided (D8): FIGI is `Asset.unique_identifier`; ticker
  is only `OpenFigiDetails.ticker` / `AssetSnapshot.ticker`, used for lookup/display, not canonical
  identity. This holds **by construction** in this project's registration
  (`_register_asset_from_match` upserts `unique_identifier=figi`). To be robust to any other
  convention, `src/assets/resolution.py::assets_for_ticker` resolves a ticker via **both**
  `OpenFigiDetails.ticker` **and** `Asset.unique_identifier` (deduped by uid; FIGIs and tickers
  never collide, so the second path is a safe no-op for FIGI-keyed assets).
- **Holdings category membership order (was OQ-3)** — decided: order does not matter. Holdings
  categories are unordered universes in this project. Do not rely on insertion order from
  `search_asset_category_memberships` / `AssetCategoryMembership.filter`. If future workflows need
  ranking, provider file order, or weights, that belongs in a dedicated holdings detail table or
  DataNode, not in category membership order.
- **AssetType source (was OQ-4)** — decided: upsert `AssetType` before upserting each public
  equity asset, and derive the canonical type from OpenFIGI `security_market_sector`, not from
  Alpaca `asset_class` or OpenFIGI `security_type`. Current explicit mapping is
  `Equity -> equity`. `security_type` and `security_type_2` remain provider classification
  details in `OpenFigiDetails`.
- **OpenFIGI classification literals (was OQ-8)** — decided: obsolete as a legacy SDK parity
  question. Do not chase removed `MARKETS_CONSTANTS.FIGI_*` values. The project uses local
  OpenFIGI response values directly: `marketSector="Equity"`, `securityType="Common Stock"`,
  and `securityType2 in {"ETP", "REIT"}`. These values only classify/filter OpenFIGI candidates
  and support `security_market_sector -> AssetType`; they never define asset identity.
- **Runtime attachment selectors (was OQ-11)** — decided: use backend SQLAlchemy table/storage
  classes in `msm.start_engine(models=[...])`, never typed row API classes. Built-in ms-markets
  tables/storage classes are used as-is (`AssetTypeTable`, `AssetTable`,
  `OpenFigiAssetDetailsTable`, `AssetSnapshotsStorage`, `AssetCategoryTable`,
  `AssetCategoryMembershipTable`). Project-local Alpaca bars storage classes are added through
  `project_storage_models()` and own `__markets_storage_app__`; downstream code must not override
  `__markets_storage_app__` on built-in ms-markets tables. Live `start_engine` attach remains a
  backend verification step after migrations are applied.
- **Read frame shape (was OQ-12)** — decided from installed SDK source. Asset-indexed storage
  writes validate frames with index `["time_index", "asset_identifier"]`; reads through
  `TimeIndexMetaTable.get_data_between_dates_from_node_identifier` use
  `dimension_filters={"asset_identifier": [asset_unique_identifier]}` and return
  `(pd.DataFrame(all_results), TimeIndexMetaTable)`. The SDK helper does not set an index on the
  returned `DataFrame`, so API chart serialization reads `time_index` as a column.

## Remaining open questions (resolve during the owning phase)

None.

## Non-goals

- No one-off scripts for main workflows.
- No custom assets for public Alpaca equities when FIGI is missing.
- Do not widen `ms-markets` `AssetTable` with Alpaca/OpenFIGI provider fields (use
  `OpenFigiDetails` / `AssetSnapshot`).
- No category membership as price-routing logic.
- No non-namespaced DataNode writes before migrations + namespaced validation.
- Do not hide storage identity in `DataNodeConfiguration`.

## First implementation slice (safest order)

1. Phase 1 (deps + settings/secrets) — no behavior change, unblocks the FIGI constants + Secret.
2. Phase 2 (storage factory + migration provider + runtime bootstrap helper).
3. Phase 3 (node rewrite + support changes), index-level rename, plan-only CLI path.
4. Validate storage metadata + frame construction locally (namespaced).
5. Then Phases 4→5→6→7; Phase 8 in parallel; Phase 9 last.
