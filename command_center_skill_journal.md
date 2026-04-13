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

### 2026-04-13

- Context:
  Create a live `app-component` widget in the `Alpaca Assets Registry` workspace so a user can register one asset by ticker through this project's FastAPI route `POST /v1/app-components/assets/register-ticker`.
- Observation:
  The updated Command Center skills were sufficient. Registry-first verification worked as intended and correctly surfaced that the widget needed a saved `bindingSpec`, not only `method`, `path`, and `mainSequenceResourceRelease.releaseId`. The widget was mounted successfully in workspace `3` with release `27` and image `43`.
- Impact:
  The workspace part is complete, but the operational path exposed two non-skill issues:
  `mainsequence project sync` is broken in this SDK build because it installs `uv`, runs `uv sync`, then fails on `uv export` after `uv` has been removed from `.venv/bin/uv`;
  `mainsequence project project_resource list` and `create_fastapi` did not agree on the eligible FastAPI resource id for the same pushed commit, so the non-interactive release path failed until the interactive selector exposed the correct resource id.
- Proposed skill improvement:
  None for the skill itself right now. The skills pointed to the right workflow. The follow-up belongs to SDK/platform issue tracking, not Command Center skill wording.
- Status:
  Completed. Workspace `3` now contains the live `register-asset-by-ticker` AppComponent widget backed by FastAPI release `27`. SDK/platform follow-up remains open for the `project sync` failure and the FastAPI resource-id mismatch.
