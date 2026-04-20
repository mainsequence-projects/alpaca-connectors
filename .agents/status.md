# Project Status

## Verified

- Repository path is `/Users/jose/mainsequence/main-sequence-workbench/projects/alpaca-connectors-153`.
- Main Sequence project detection reports project ID `153`.
- Local SDK version in `requirements.txt` is `3.18.8`, matching the latest GitHub version reported by the CLI.
- `mainsequence project refresh_token --path .` completed successfully on 2026-04-20.
- `POST /v1/charts/lightweight/ohlc` now has one explicit FastAPI response model:
  `LightweightOhlcResponse`.
- The local OpenAPI route response schema references `LightweightOhlcResponse` directly and does not use a route-level `anyOf`.
- `LightweightOhlcResponse` exposes the structured `spec` field only; the duplicate stringified spec field has been removed from API models, service output, docs, tests, and saved workspace response ports.
- All schema-visible API routes under `/health` and `/v1/` declare response models.
- `python -m unittest discover tests` passed 45 tests on 2026-04-20 after the duplicate stringified spec removal.

## Pending

- The updated API schema has not been released as a new FastAPI `ResourceRelease` in this turn.
- Live Command Center behavior was not rechecked after the local API contract change.

## Notes

- The route intentionally keeps the generated Command Center form query-only for the OHLC AppComponent flow.
- The API does not use `LoggedUserContextMiddleware` because current route handlers do not read `request.state.user`.
