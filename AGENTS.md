# AGENTS.md

## CodeRepository-Specific Instructions

This repository is the Alpaca connector project for Main Sequence: Alpaca US equity Assets,
brokerage Accounts and holdings, ETF Universes, stock bars, Universe-backed ETF weight signals and
analytical ETF portfolios. The installable `alpaca_connectors` package (`src/`) backs the
`alpaca-connectors` CLI, the FastAPI application with its MCP server, and the workflow Jobs.

Repository rules:

- Capability boundaries are Assets, Universes, Market Data, Accounts, Holdings, Portfolios and
  Operations; providers are dependencies, not a `Connection` domain. API-only capability
  descriptions live in `alpaca_connectors.api.app`; the rest of the package is reusable behavior.
- Import capabilities from `alpaca_connectors.{universes,market_data,account,holdings,portfolios}`;
  never reintroduce a generic top-level `src`, `api` or `migrations` package.
- Frozen identities: the provider `alpaca_connectors.migrations:migration` keeps catalog key
  `package="src"`, and `SIGNAL_IDENTITY_CLASS_IMPORT_PATH` (`src.portfolios.alpaca_etf_signal`)
  keeps existing signal UIDs stable. Never change either.
- Keep planning, registration, holdings and bars logic in `alpaca_connectors`; ETF extraction
  comes from `etfhextractor`. The CLI, routes and Jobs only orchestrate; add no one-off scripts.
- Only `.mainsequence/workflows/*.yaml` creates Jobs and sets their schedules; code never does.
  A configuration run (`operations/configuration_jobs.py`) selects it with `--configuration-uid`;
  JobRuns never own configuration. `src/jobs/` launchers run by path and are bound to live Jobs,
  so never move them. Every launcher except the migration Job starts with `skip_if_schema_behind`.
- Hosted schema changes come only from the workflow's migration Job (ADR 0012); never run
  `metatables migrations upgrade` or `downgrade` against a hosted environment. Revisions add first
  and drop later. Use `metatables` for MetaTables interfaces. Tables belong to the Environment's
  Team, never to one workload User.

FastAPI surface:

- Routes stay thin and contract-driven over the package services, one `/v1/<capability>` group
  per capability ([API](docs/api.md#resources)).
- `create_app()` installs `install_request_identity` once after the routers (the platform launcher
  refuses an app without it), then `install_mcp`. Routes read the caller only through
  `alpaca_connectors.api.app.identity`, with no header, `request.state` or process-user fallback.
  No route is public. Call a local API with `Authorization: Bearer $(mainsequence auth token)`.
- Authorship and ownership follow the acting user: the requester on an Agent's call, otherwise
  the caller, never both. No UID comes from a body, header or tool argument.
- The workflow key `alpaca` is the Agent's MCP tool prefix (`^[a-z][a-z0-9_]{0,39}$`); sites name
  the release by repository and key. `GET /v1/project-state/configuration` returns `agent_uid`
  and `organization_environment_uid` from `api.app.site_agent`; never put either in a site build.

Project-to-agent boundary:

- One Harness Agent: `.agents/agent_card.json`, six skills under `.agents/skills/mcp_tools/`, and
  the tools `alpaca_connectors.api.app.mcp_server` serves from `alpaca_connectors.agent_tools`.
  Add no separate `agents/` runtime and no `.tau/extensions` tools.
- The API publishes only `.agents/skills/mcp_tools/` through `skills_extension`: one copy per
  skill, named after its frontmatter, with no custom loader or shim. Never publish the whole
  `.agents/skills/` tree, copy skills into `.tau/skills`, or expose SDK or platform skills.
- The `harness_agent` in `alpaca-connectors-api.yaml` serves `api/agent/main.py`
  (`ms_tau_sdk.create_app()`) with `acts_for_requester: true` and
  `mcp_applications: [{resource: alpaca}]`. It deploys after the API, the two name each other in
  `access.resources`, and `TAU_EXCLUDE_BASE_TOOLS` and `TAU_EXCLUDE_MAINSEQUENCE_MCP` stay `true`.
- Tool invariants:
  - No tool or CLI command takes Alpaca key values. Only the HTTP account create and rotate routes
    do, to store managed Secrets (ADR 0011), and nothing echoes, logs or persists them. The
    Agent's key entry is `alpaca_account_key_request` and the site (ADR 0015).
  - Results and errors never quote arguments. Deletes need exact `DELETE <uid>`. Bars, signal and
    portfolio updates submit Jobs. Reads run in parallel and mutations in sequence.
  - There is no Environment selector and no trading, ordering, arbitrary SQL, shell or platform
    release.

Validate with `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check api src tests`,
`.venv/bin/ruff format --check api src tests` and `.venv/bin/mkdocs build --strict`. Developer
docs: [operator guide](docs/operations/operator-guide.md) (CLI rules and operational notes),
[development](docs/operations/development.md), [Jobs](docs/operations/jobs.md), [API](docs/api.md)
and [Agent](docs/agent.md). Main Sequence SDK guidance: `.agents/skills/mainsequence/`.

## The Alpaca Agent

This repository also deploys the Alpaca Agent. Its runtime instructions are `.tau/SYSTEM.md`; this
file is for developers. Tau loads this file into the Agent's context too, so keep it short and
never put runtime-only instructions here.
