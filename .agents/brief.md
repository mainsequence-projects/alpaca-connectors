# Project Brief

## Current Goal

Keep the Alpaca Connectors FastAPI surface aligned with Main Sequence API standards and Command Center usage.

## Success Condition

- Schema-visible API routes declare explicit response models.
- Command Center-facing routes return typed response bodies rather than loose dictionaries.
- Local tests confirm the OpenAPI contract and route behavior.

## Scope

In scope:

- FastAPI route contracts under `api/app/`.
- API documentation under `docs/api.md`.
- Local validation evidence.

Out of scope unless requested:

- Creating a new FastAPI project resource release.
- Mutating the live Command Center workspace.
