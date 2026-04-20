# Command Center Skill Journal

Purpose:

- record issues, gaps, and improvement ideas related to the Command Center skill set
- keep a running log of decisions that should feed back into the local skills or upstream scaffold
- capture concrete examples from this project that exposed missing guidance

## Entry Template

### YYYY-MM-DD

- Context:
- Observation:
- Impact:
- Proposed skill improvement:
- Status:

## Entries

### 2026-04-20

- Context:
  Simplify the OHLC AppComponent form after the live widget showed both ticker/date inputs and the old body fields (`unique_identifier`, duplicate dates, and `node_identifier`).
- Observation:
  The FastAPI route exposed both a JSON body model and query parameters, so OpenAPI discovery could generate a mixed form even though the intended interaction is only ticker plus date range. The AppComponent should not ask users for `unique_identifier` or `node_identifier`; the API can resolve the asset unique identifier from the ticker inside `HOLDINGS__IVV` and use the known default node `alpaca_stock_bars_1d_sip_all`.
- Impact:
  Local OpenAPI for `POST /v1/charts/lightweight/ohlc` now exposes only `ticker`, `start_date`, and `end_date` and has no request body. Workspace `3` was updated at `2026-04-20T18:06:00.998281Z`; the saved `alpaca-ohlc-chart-request` binding spec keeps Select2 on `query:ticker`, with `query:page` and `query:limit` as hidden pagination fields.
- Proposed skill improvement:
  For AppComponent-backed routes, avoid exposing fallback body models in OpenAPI when the desired user form is a query-only workflow. Put internal defaults behind `include_in_schema=False` or resolve them server-side.
- Status:
  Implemented locally, tested, and applied to workspace `3`. A new FastAPI release is still required before deployed OpenAPI discovery reflects the local API schema change.

### 2026-04-20

- Context:
  Investigate Command Center AppComponent error: "The target /openapi.json response did not look like an OpenAPI document."
- Observation:
  Local FastAPI schema generation is valid: `GET /openapi.json` returns HTTP 200 JSON with `openapi: 3.1.0`, and `tests.test_api_app` passes. The live workspace still referenced FastAPI release `29`, but `ResourceRelease.get(29)` now returns 404. The current FastAPI release is `31` for resource `437` on project `153`.
- Impact:
  Workspace `3` was updated so both AppComponents target release `31` instead of deleted release `29`. A direct unauthenticated request to `https://api-app-437.fapi-development.main-sequence.app/openapi.json` returns 401 JSON, so any remaining OpenAPI-discovery failure after the release-id fix should be treated as session-JWT/header propagation rather than a local schema-generation problem.
- Proposed skill improvement:
  None for the Command Center payload skill. This exposed an operational follow-up: stale `mainSequenceResourceRelease.releaseId` values can produce OpenAPI-looking client errors even when local FastAPI schema generation is healthy.
- Status:
  Completed. Workspace update returned `Updated At: 2026-04-20T17:53:05.610028Z` and live detail shows both AppComponents with `releaseId: 31`.

### 2026-04-20

- Context:
  Extend workspace `3` (`Alpaca Assets Registry`) with an AppComponent-driven OHLC chart flow: ticker search/select, start/end date inputs, and a bound `lightweight-charts-spec` widget rendered from this project's FastAPI route `POST /v1/charts/lightweight/ohlc`.
- Observation:
  Registry detail for `app-component` and `lightweight-charts-spec` was sufficient to confirm the contract: the chart widget consumes `props-json` as `core.value.json@v1`, and the AppComponent can publish `response:$` for downstream binding. The AppComponent async select writes the selected option label into the request field, so the API resolves the selected ticker/FIGI/identifier strictly inside `HOLDINGS__IVV` before querying bars.
- Impact:
  Workspace `3` now contains three widgets: `register-asset-by-ticker`, `alpaca-ohlc-chart-request`, and `alpaca-ohlc-chart`. Both AppComponents target FastAPI release `29`; the chart widget binds `props-json` to `alpaca-ohlc-chart-request` output `response:$`.
- Proposed skill improvement:
  None. The registry-first workflow was correct. Operationally, `mainsequence project images create` still has a CLI gap when no `ProjectBaseImage` rows exist: the CLI prompt path fails with `No options available for Base image id`, while the backend SDK image-create path succeeds with `base_image_id=None`.
- Status:
  Completed. Pushed commits: `7c95289` for the API/workspace implementation and `c7a32ce` for the workspace source-of-truth release id update. Project image `1`, FastAPI resource `417`, and FastAPI release `29` were created.

### 2026-04-13

- Context:
  Create a live `app-component` widget in the `Alpaca Assets Registry` workspace so a user can register one asset by ticker through this project's FastAPI route `POST /v1/app-components/assets/register-ticker`.
- Observation:
  The updated Command Center skills were sufficient. Registry-first verification worked as intended and correctly surfaced that the widget needed a saved `bindingSpec`, not only `method`, `path`, and `mainSequenceResourceRelease.releaseId`. The widget was mounted successfully in workspace `3` with release `27` and image `43`.
- Impact:
  The workspace part is complete, but the operational path exposed two non-skill issues:
  `mainsequence project sync` is broken in this SDK build because it installs `uv`, runs `uv sync`, then fails on `uv export` after `uv` has been removed from `.venv/bin/uv`;
  `create_fastapi` filters eligible resources by exact `repo_commit_sha == related_image.project_repo_hash`, and this backend currently stores `ProjectResource.repo_commit_sha` for the same `api/app/main.py` file in mixed short and full commit-hash formats. Exact example:
  `mainsequence project project_resource list --filter resource_type=fastapi` resolved upstream HEAD to `repo_commit_sha=adb3fbbbb2b35255aef71ab197eeeba408bf08a3` and effectively queried `ProjectResource.filter(project__id=153, repo_commit_sha="adb3fbbbb2b35255aef71ab197eeeba408bf08a3", resource_type="fastapi")`, which surfaced resource `384`;
  `mainsequence project project_resource create_fastapi --related-image-id 43` used image `43`, whose `project_repo_hash` was the short hash `adb3fbb`, and effectively queried `ProjectResource.filter(project__id=153, repo_commit_sha="adb3fbb", resource_type="fastapi")`, which surfaced resource `379`;
  the backend rows at that point were both present for the same file:
  `379 -> path=api/app/main.py, repo_commit_sha="adb3fbb"`
  `384 -> path=api/app/main.py, repo_commit_sha="adb3fbbbb2b35255aef71ab197eeeba408bf08a3"`;
  because the CLI does exact string equality on `repo_commit_sha`, passing `--resource-id 384 --related-image-id 43` failed with `Selected resource does not match the selected image commit and release type.`
- Proposed skill improvement:
  None for the skill itself right now. The skills pointed to the right workflow. The follow-up belongs to SDK/platform issue tracking, not Command Center skill wording.
- Status:
  Completed. Workspace `3` now contains the live `register-asset-by-ticker` AppComponent widget backed by FastAPI release `27`. SDK/platform follow-up remains open for the `project sync` failure and the FastAPI resource-id mismatch.
