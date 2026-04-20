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
