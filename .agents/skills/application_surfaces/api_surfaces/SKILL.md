---
name: alpaca-api-surfaces
description: Use this skill when the task is about the Alpaca Connectors FastAPI surface. This skill owns the existing thin HTTP layer in `api/app/` that wraps project registration, holdings-category, discovery, and chart workflows. It does not own the producer-side Alpaca, ETF extraction, or DataNode business logic under `src/` and `etf_extraction/`.
---

# Alpaca API Surfaces

## Overview

Use this skill when the task is about the repository's FastAPI support surface under `api/app/`.

The API in this project is intentionally thin:

- route handlers validate input
- services call the existing reusable project logic
- response models stay explicit and widget-friendly

This skill is for changing or extending that API without moving business logic out of the existing
reusable modules.

## This Skill Can Do

- update `api/app/main.py`, `api/app/schemas.py`, and `api/app/services.py`
- keep the API aligned with the existing project CLI and reusable modules
- define or tighten request and response models
- keep FastAPI handlers thin and contract-driven
- adjust the API documentation under `docs/api.md`

## This Skill Must Not Claim

- that the API can do anything the local project services do not already support
- that the API is a live released platform surface unless that was separately verified
- ownership of producer-side Alpaca registration, ETF extraction, or DataNode update logic

## Existing Supported Endpoints

- `GET /health`
- `GET /v1/discovery/config`
- `POST /v1/assets/registration/execute`
- `POST /v1/app-components/assets/register-ticker`
- `POST /v1/holdings-categories/execute`
- `POST /v1/charts/lightweight/ohlc`

## Working Rules

- keep route handlers thin; push reusable logic into `src/` or `api/app/services.py`
- prefer extending existing request and response models instead of returning loose dictionaries
- do not rebuild the CLI workflows independently inside the API
- keep Command Center-specific request and response behavior aligned with the existing project
  widget and AppComponent usage

## Examples

- "Add one more read-only discovery field to `/v1/discovery/config`."
- "Adjust the single-ticker registration API response contract without changing the underlying
  registration logic."
- "Update the lightweight OHLC API so it stays aligned with the existing chart widget payload."

Do not handcraft loose JSON and hope the widget accepts it.

For generic Command Center tabular consumers, use:

```python
from mainsequence.client.command_center.data_models import TabularFrameResponse
```

Declare `response_model=TabularFrameResponse` when the route returns the full
`core.tabular_frame@v1` payload.

Only fall back to a generic business response model when:

- the user explicitly asks for a non-widget API contract, or
- the client is clearly not a Main Sequence widget and the route should remain UI-agnostic
- no existing Command Center SDK response model fits the endpoint, and that mismatch is explicit in the task reasoning

If the endpoint participates in an AppComponent flow, also use the AppComponents skill to validate the input-form side of the contract.

### 6.1 Use notification response contracts for immediate client feedback

When an API action needs immediate user-facing acknowledgment, prefer a response contract carrying:

- `"x-ui-role": "notification"`

Use the SDK `NotificationDefinition` model for this immediate feedback path instead of returning a loose acknowledgment dictionary.

This is the default response-side feedback contract for:

- AppComponent-triggered actions
- Command Center actions that should acknowledge success, warning, validation failure, or initiation of work

Do not force this onto read-only data endpoints or widget data endpoints whose job is to return structured business data rather than action feedback.

### 6.2 Use platform notifications for long-running asynchronous updates

If the work continues after the request returns, spans subprocesses, or becomes a long-running background workflow, do not rely on the immediate HTTP response as the ongoing user-feedback channel.

Use `mainsequence.client.Notification` for asynchronous user updates in those cases.

The immediate API response may still acknowledge that work started, but ongoing or delayed user feedback belongs in platform notifications rather than a one-shot HTTP response model.

### 7. Keep FastAPI documentation rich

For FastAPI routes in a Main Sequence project:

- route summaries should be explicit
- route descriptions should be explicit
- router parameters should always be explicitly typed
- router parameters should always have rich FastAPI metadata such as `description`
- use `Query(...)`, `Path(...)`, `Body(...)`, and similar parameter helpers instead of leaving route parameters undocumented
- add examples, bounds, enums, and other validation metadata when they clarify the contract
- request and response models should be documented
- route examples should be added when they materially clarify the contract
- the API folder should have a `README.md` explaining what the API does

## Review Rules

When reviewing an API change, look for:

- a Command Center-facing API being treated like a generic standalone API
- missing use of an existing Command Center SDK response model when the endpoint could have matched it
- missing load/use of the AppComponents skill when the endpoint is part of an AppComponent flow
- missing load/use of the workspace-builder skill when the endpoint is tied to a mounted workspace widget
- missing load/use of the Adapter from API skill when the endpoint is meant for connection-first Command Center data consumption
- a non-FastAPI proposal without an explicit repository reason
- workspace-visualization endpoints placed outside a dedicated `/workspace` route group without a clear repository reason
- route handlers doing too much
- missing `response_model`
- `LoggedUserContextMiddleware` added even though the route does not consume `request.state.user`
- route parameters that are untyped or weakly documented
- generic response models where a widget-facing contract should have been used
- tabular Command Center data routes returning a full frame without `TabularFrameResponse`
- action endpoints returning loose dict acknowledgments where `NotificationDefinition` should have been used
- long-running or spanning subprocess workflows trying to use a one-shot HTTP response instead of `mainsequence.client.Notification` for asynchronous user updates
- producer logic duplicated inside routes
- missing request-user middleware when request-local user access is required
- widget-facing payloads built without the SDK contract model
- API routes that really belong in workspace or AppComponent flows instead
- Command Center-facing APIs being treated as complete before a FastAPI resource and FastAPI `ResourceRelease` exist

## Validation Checklist

Do not claim success until you have checked:

- the API is being implemented as FastAPI
- the intended consumer is explicit, and Command Center was used as the default assumption unless the user overrode it
- an existing Command Center SDK response model was used whenever the endpoint could reasonably match it
- full `core.tabular_frame@v1` routes use `TabularFrameResponse`
- route inputs are intentionally typed
- route parameters are described richly
- every structured route has an explicit `response_model`
- route handlers are thin
- `APIDataNode` is used when the route reads a published DataNode
- `SimpleTableUpdater.execute_filter(...)` is used when the route reads simple-table rows
- middleware is present when request-local user context is required
- middleware is absent when the route does not consume `request.state.user`
- endpoints built specifically for workspace visualizations live under `/workspace`
- immediate client feedback endpoints use `NotificationDefinition` with `"x-ui-role": "notification"` when the route is acknowledging an action rather than returning business data
- long-running or subprocess-spanning workflows use `mainsequence.client.Notification` for asynchronous user updates instead of relying on a one-shot HTTP response
- widget-facing endpoints use exact SDK response contracts
- APIs consumed through Adapter from API expose the well-known Command Center connection contract
- local API docs or route behavior reflect the intended contract
- any Command Center-facing API that is meant to be usable now already exists as a FastAPI project resource
- any Command Center-facing API that is meant to be usable now already has a FastAPI `ResourceRelease`

If the endpoint feeds a widget directly, also check:

- the correct SDK response model is used
- the row shape matches the widget contract
- the payload is validated through the FastAPI boundary

If the endpoint is part of an AppComponent or workspace flow, also check:

- the related Command Center skill was loaded
- the API contract does not drift from the AppComponent or workspace expectations

## This Skill Must Stop And Escalate When

- the proposed API framework is not FastAPI and the repository does not explicitly justify that choice
- the likely consumer is Command Center but the task is being treated as a generic API without loading the relevant Command Center skill
- a route is being left without a `response_model`
- `LoggedUserContextMiddleware` is being added without any concrete route-level need for `request.state.user`
- workspace-visualization endpoints are being mixed into unrelated route groups without a clear reason
- route parameters are being left effectively undocumented
- the API route is duplicating producer logic instead of consuming published resources
- the endpoint is supposed to feed a widget but no exact contract model is available
- request-local user context is required but the request binding path is unclear
- the task is actually about workspace mutation or AppComponent form design
- docs and code disagree on the boundary contract
- the API is expected to be usable from Command Center now, but the FastAPI resource or FastAPI `ResourceRelease` does not exist yet

Do not guess through API contracts.
