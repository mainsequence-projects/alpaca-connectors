# Tasks

## Open

### Publish Updated FastAPI Response Contract

- Owning skill: `.agents/skills/platform_operations/orchestration_and_releases/SKILL.md`
- Scope: create or update the FastAPI project resource release after the `/v1/charts/lightweight/ohlc` response-model change is committed and synced.
- Expected output: a new FastAPI `ResourceRelease` for the current API code, with the relevant Command Center workspace/AppComponent targeting that release if needed.
- Validation evidence: `mainsequence project project_resource list --filter resource_type=fastapi`, the created release id, and a successful `/openapi.json` check showing the chart route has a `200` response body schema referencing `LightweightOhlcResponse` with structured `spec` only.

### Recheck Live Command Center AppComponent Contract

- Owning skill: `.agents/skills/command_center/app_components/SKILL.md`
- Scope: after the FastAPI release is updated, verify the live AppComponent sees response schemas for `POST /v1/charts/lightweight/ohlc`.
- Expected output: confirmation that OpenAPI discovery exposes only `ticker`, `start_date`, `end_date` inputs and the single `LightweightOhlcResponse` response body with structured `spec` only.
- Validation evidence: registry/workspace inspection plus live AppComponent behavior or a captured `/openapi.json` response from the released API.

## Completed

### Add Response Body Model To Chart API Route

- Owning skill: `.agents/skills/application_surfaces/api_surfaces/SKILL.md`
- Scope: make the schema-visible chart route declare one typed response model and return that Pydantic model directly.
- Expected output: local OpenAPI contains a single response body schema for selector and chart modes, with structured `spec` only.
- Validation evidence: `python -m unittest tests.test_api_app` and `python -m unittest discover tests` passed on 2026-04-20.
