# Project Status

## Verified

- The repository resolves to CodeRepository `c4980dd0-db33-420c-9a3f-050815a03512`, branch
  `main`, CodeRepositoryBranch `945bfddd-5f1f-4541-a87a-faea3af6271f`.
- Runtime versions are Python 3.13.11, Main Sequence SDK 8.0.7, and ms-markets 1.0.2.
- `pyproject.toml` uses compatible lower bounds and does not exactly pin Main Sequence packages
  or the `etfhextractor` Git dependency.
- The project capability boundaries are Project State, Assets, Universes, Market Data, Accounts,
  Holdings, Portfolios, and Operations.
- Reusable application behavior lives under `src/`; the installed CLI and thin FastAPI routers
  call those services.
- Raw Alpaca key values are absent from CLI flags and API request/response schemas. Account
  registration stores only Main Sequence Secret names plus a non-reversible key fingerprint.
- Account metadata refresh and canonical ms-markets holdings capture are independent operations.
- Historical prices are published by an ms-markets `AssetIndexedDataNode`. Update output is derived
  from the stored frequency/feed/adjustment profile; MetaTable UIDs remain read-only query
  identifiers, and credentials come from the configuration's registered account.
- Alpaca bar configurations are durable MetaTable rows with UUID identity and one of three asset
  sources: explicit Assets, an active materialized universe, or the configured account's newest
  persisted non-cash holdings snapshot in the inclusive trailing 30-day window.
- Universe targets are rows in `UniverseSourceTable`. Runtime extraction and synchronization do
  not read a Python ticker list or ticker/provider map.
- The optional IVV bootstrap record is package data reconciled only by the explicit, idempotent
  `universe-source seed-defaults` command and uses the official US iShares product page.
- FastAPI exposes resource collections, discovery contracts, CRUD/actions, stable errors,
  caller-sensitive discovery cache headers, and the current static-site CORS policy.
- The discovery, collection, bulk-preflight, and bulk-execution payloads validated against the
  pinned Command Center 0.1.18 manifest schemas, including rejection of its invalid fixtures.
- The API 2.1.0 workflow declaration validated with no backend errors or warnings. Its explicit
  automatic-redeployment policy is enabled with a null tag regex, admitting every otherwise-valid
  synchronized commit.
- Backend migration revision `0004 (head)` is applied. Finalization reports all project
  MetaTables active, zero reserved, and zero failed:
  - `alpaca_connectors__bars_1d_sip_all`
  - `alpaca_connectors__bars_1d_iex_raw`
  - `alpaca_connectors__bars_configuration`
  - `alpaca_connectors__bars_configuration_asset`
  - `alpaca_connectors__acct_alpaca`
  - `alpaca_connectors__universe_source`
  - `alpaca_connectors__asset_registration_operation`
- Bar-configuration MetaTable UIDs:
  - configuration: `b24c4b08-f8b1-44ce-9160-324834f0ae86`
  - explicit membership: `933b62b8-1c5b-46b4-a248-db12ee56edc3`
- UniverseSource MetaTable UID:
  `630be5b8-5cb7-4bfc-99ea-8b84cd879c9d`.
- The seeded IVV source exists as UID `28f98530-9380-4a3b-b70e-3da8b972c205` and its live row
  points to `https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf`.
- A live read-only preview of that source returned 504 component symbols with an as-of date of
  September 1, 2026. A second seed run preserved `updated_at`, proving the reconciliation is
  idempotent after the URL correction.
- Live CLI and FastAPI read checks return one source, zero registered Alpaca accounts, and two
  migrated daily price datasets. Both price datasets currently contain zero rows.
- Local verification: 115 tests pass; Ruff passes; strict MkDocs build passes.
- Streamlit was not removed or refactored.

## Open Evidence And Blockers

- No Alpaca account is currently registered in the target environment. Creating a valid bar
  configuration, capturing holdings, and writing prices require the operator to select the intended
  paper/live Secret names; this implementation does not guess them or inspect an unknown account.
- The initial FastAPI ResourceRelease and its first deployment still require repository sync and
  post-webhook verification; workflow validation alone is not deployment proof.
- The platform injects authenticated request identity and gates the ResourceRelease. A concrete
  application edit-policy for per-route mutation authorization has not been selected; the code does
  not invent user/team RBAC rules.
