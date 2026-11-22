# alpaca-connectors

`alpaca-connectors` connects Alpaca-backed assets, market data, brokerage accounts, holdings,
order execution, and portfolios built from weights an application provides to Main Sequence and
ms-markets.

## Capabilities

- **Project State:** API health, capability catalog, migrated datasets, and account count.
- **Assets:** provider-native Alpaca UUID identity, required Alpaca details, the trading calendar
  of each asset's class, and optional OpenFIGI enrichment.
- **Market Data:** maintain reusable bar configurations and query or publish Alpaca OHLCV
  observations in migrated asset-indexed tables. A configuration fetches explicit assets, an
  account's recent holdings, or every asset of the portfolios that name it.
- **Accounts:** register and maintain Alpaca accounts from submitted keys, or from existing
  Main Sequence Secrets imported once by name; the keys are stored encrypted on the account row.
- **Holdings:** capture Alpaca positions and cash as immutable ms-markets account snapshots; the
  account sync also sizes the account's target and allocates its funds
  ([ADR 0023](docs/adrs/0023_account_sync_sizes_and_allocates_funds.md)).
- **Execution:** place, sync and cancel batches of market orders on a registered account for
  authorised applications, keeping the ms-markets execution ledger
  ([ADR 0016](docs/adrs/0016_order_execution_for_authorised_applications.md)). The Alpaca Agent has
  no tool over it.
- **Portfolios:** run portfolios built from weights an application provides
  ([ADR 0024](docs/adrs/0024_portfolios_from_provided_weights.md)), the connector's only kind
  ([ADR 0027](docs/adrs/0027_one_portfolio_path.md)). The application registers the assets,
  creates the portfolio over HTTP and owns it; the connector values it on persistent interpolated
  Alpaca bars with reusable persisted-calendar rebalance configurations. One Job declared in the
  API workflow runs every configuration, and its dispatcher starts the daily runs
  ([ADR 0013](docs/adrs/0013_workflow_declared_configuration_jobs.md)). Deleting a portfolio
  retires its `Portfolio` first.

## Repository Boundaries

- `src/alpaca_connectors/assets/`: instrument discovery, FIGI resolution, strict registration, and trading calendars.
- `src/alpaca_connectors/market_data/`: migrated price storage, updater, queries, and account-backed execution.
- `src/alpaca_connectors/account/`: account identity, encrypted credential storage, registration, and refresh.
- `src/alpaca_connectors/holdings/`: Alpaca-position translation and canonical snapshot publication.
- `src/alpaca_connectors/execution/`: order batches on registered accounts and the ms-markets execution ledger.
- `src/alpaca_connectors/portfolios/`: portfolios from provided weights: configuration, construction, and valuation.
- `src/alpaca_connectors/cli/`: thin command adapters over reusable services.
- `src/jobs/`: reviewed launchers for platform-managed execution.
- `src/alpaca_connectors/api/`: FastAPI-only contracts, pagination, routing, and response shaping.
- `api/app/main.py`: path-stable entry point for the deployed FastAPI resource (not in the wheel).
- `src/alpaca_connectors/agent_tools.py`: structured project tools served by the API's `/mcp`.
- `api/agent/main.py`: TAU Harness Agent entry point (not in the wheel).
- `.mainsequence/workflows/`: backend-validated Job, FastAPI, and Harness Agent deployment declarations.
- `docs/`: architecture and operating instructions.

The sibling Command Center static application consumes the FastAPI collection and discovery
contracts without moving business logic into the frontend.

## Alpaca Agent

The **Alpaca Agent** is a TAU Harness over the existing application, not a second implementation.
Its four business skills use 13 structured tools through the Alpaca API's MCP endpoint. REST, MCP,
and CLI adapters reuse the same services; long bars and portfolio updates return JobRuns. Account
key values are entered on the site, never in the Agent's conversation or tool arguments.

| Skill | Tools |
| --- | --- |
| `alpaca-asset-registration` | `alpaca_query_assets`, `alpaca_register_assets` |
| `alpaca-account-workflow` | `alpaca_query_accounts`, `alpaca_manage_account`, `alpaca_account_key_request` |
| `alpaca-stock-bars` | `alpaca_query_bar_configurations`, `alpaca_manage_bar_configuration` |
| `alpaca-portfolios` | `alpaca_query_rebalance_configurations`, `alpaca_manage_rebalance_configuration`, `alpaca_query_portfolios`, `alpaca_manage_portfolio` |

Every skill starts from `alpaca_project_state`, and the bars and portfolio skills follow their Jobs
with `alpaca_get_job_run_status`. The Agent runs a portfolio, pauses, resumes or deletes one for
its owner, and never creates one or gives it weights.

See [Agent architecture and boundaries](docs/agent.md) for requester versus workload identity,
deployment, timeouts, and the enforced exclusion of generic coding/shell and platform MCP tools.
The Harness retains only skill-scoped reading and SDK A2A Task controls alongside the Alpaca
application tools.

## Install And Migrate

Python 3.13 is required. Dependency ranges remain unlocked in `pyproject.toml`; `uv.lock` records
the reproducible resolution. The project runs on Main Sequence SDK 9, ms-markets 4, and the
`mainsequence-metatable` client, which provides the `metatables` package and migration CLI.

```bash
uv sync
mainsequence refresh-token
metatables --local migrations upgrade --provider msm_migrations:migration head
metatables --local migrations upgrade --provider alpaca_connectors.migrations:migration head   # local runtime; hosted: migration Job
```

The project's migrations create every table it owns, the interpolated-price table of each bars
profile included (ADR 0029). Application startup never creates schema and never seeds rows.

## Core CLI

Account registration imports the keys once from existing Main Sequence Secrets, never accepting
key values:

```bash
alpaca-connectors account register \
  --api-key-secret-name ALPACA_PAPER_API_KEY \
  --secret-key-secret-name ALPACA_PAPER_SECRET_KEY \
  --paper \
  --plan-only

alpaca-connectors account list
alpaca-connectors account refresh <ACCOUNT_UID>
alpaca-connectors holdings capture --account-uid <ACCOUNT_UID> --execute
```

Create a reusable update configuration and run it:

```bash
alpaca-connectors market-data dataset list
alpaca-connectors market-data bar-configuration create \
  --name "Daily account holdings" \
  --account-uid <ACCOUNT_UID> \
  --asset-source account_holdings \
  --frequency 1d \
  --feed sip \
  --adjustment all
alpaca-connectors market-data update \
  --configuration-uid <CONFIGURATION_UID> \
  --execute
alpaca-connectors market-data prices \
  --dataset-uid <META_TABLE_UID> \
  --asset-uids <ASSET_UID>
```

The FastAPI update action queues the generic `Alpaca Bars Update` Job with only the stored
configuration UID. Its JobRun can be polled at `/v1/operations/job-runs/{job_run_uid}`; the API
does not execute the bars update in its own process.

Exact asset registration:

```bash
alpaca-connectors asset register --account-uid <ACCOUNT_UID> --symbols NVDA,AAPL
```

An application registers the assets a portfolio needs this way, or through the registration
routes, before it names them in weights.

Create a reusable rebalance policy, then inspect, run and delete portfolios:

```bash
alpaca-connectors portfolio rebalance create \
  --name "NYSE close" \
  --strategy calendar_event_signal \
  --calendar-identifier NYSE \
  --session-label regular \
  --rebalance-event market_close \
  --rebalance-cadence every_session
alpaca-connectors portfolio list
alpaca-connectors portfolio get <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio run <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio runs <PORTFOLIO_CONFIGURATION_UID>
alpaca-connectors portfolio delete <PORTFOLIO_CONFIGURATION_UID> --execute
```

No CLI command creates a portfolio or gives it weights: the application that owns it does, over
HTTP ([API](#api)). `portfolio delete --execute` retires the portfolio's `Portfolio` before it
deletes the configuration, and is refused while an account's target names it.

The Portfolio Configuration stores calculation intent and its scheduled flag only. The
`Alpaca Portfolio` Job declared in `.mainsequence/workflows/alpaca-connectors-api.yaml` runs it: a
run carries `--configuration-uid <uid>`, and the daily scheduled run of its dispatcher (02:00 UTC)
starts one such run per configuration whose `scheduled` flag is set (its owner clears it with
`PATCH /v1/portfolio-configurations/{uid}` and `{"scheduled": false}`).
Schedule, compute, image, and automatic-deployment state belong to the declared Jobs. The current
CodeRepositoryBranch resolves Environment scope automatically; the user never supplies an
Environment UID. Rebalances use actual persisted market events and the latest observation at or
before each event; the portfolio is an analytical calculation, not live execution.

## API

```bash
uv run uvicorn alpaca_connectors.api.app.main:app --reload
```

For joint local debugging, launch **Debug Alpaca API + Command Center Site** from VS Code. It starts
FastAPI on port `8321`, the sibling Vite site on port `5421`, and opens Chrome with JavaScript
debugging enabled. See [the development workflow](docs/operations/development.md).

The API exposes `/v1/accounts`, account holdings and orders, `/v1/assets`,
`/v1/market-data/bar-configurations`, `/v1/market-data/datasets`, `/v1/portfolio-configurations`,
`/v1/portfolio-rebalance-configurations`, `/v1/operations/job-runs/{job_run_uid}`, and
`/v1/project-state`. Collections use authoritative `pageInfo`; selectable collections have
separate resource-discovery endpoints.

An application creates a portfolio with its first weights, then gives it new weights whenever it
chooses them ([Portfolios](docs/portfolios/portfolios.md)):

```bash
curl -X POST http://127.0.0.1:8321/v1/portfolio-configurations/provided-weights \
  -H "Authorization: Bearer $(.venv/bin/mainsequence auth token)" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Two-stock basket",
    "bars_configuration": {"name": "Basket bars", "account_uid": "<ACCOUNT_UID>"},
    "rebalance_configuration_uid": "<REBALANCE_CONFIGURATION_UID>",
    "observations": [
      {"observed_at": "2026-11-17T21:00:00Z",
       "weights": {"ALPACA::<ASSET_UUID_1>": 0.6, "ALPACA::<ASSET_UUID_2>": 0.4}}
    ]
  }'

curl -X POST http://127.0.0.1:8321/v1/portfolio-configurations/<PORTFOLIO_CONFIGURATION_UID>/weights \
  -H "Authorization: Bearer $(.venv/bin/mainsequence auth token)" \
  -H "Content-Type: application/json" \
  -d '{"observations": [{"observed_at": "2026-11-18T21:00:00Z",
                         "weights": {"ALPACA::<ASSET_UUID_1>": 0.5, "ALPACA::<ASSET_UUID_3>": 0.5}}]}'
```

The caller owns the portfolio: only it gives weights, pauses or deletes it. An asset the new
weights leave out is written at `0`, and an observation at or before the last one is refused.

Every request needs a verified caller, `/health` included: the platform-signed caller assertion
when hosted, a direct Bearer token checked against `MAINSEQUENCE_ENDPOINT` locally. `create_app()`
installs the Main Sequence request identity that does this once; the platform's FastAPI launcher
refuses an app without it. Give the API and the CLI the same development backend and call a local
API as `curl -H "Authorization: Bearer $(.venv/bin/mainsequence auth token)" http://127.0.0.1:8321/health`.
See [ADR 0014](docs/adrs/0014_sdk_request_identity.md) and
[Authentication](docs/api.md#authentication).

See [the documentation map](docs/SUMMARY.md) and [API reference](docs/api.md).
