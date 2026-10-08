# Alpaca Agent

The repository exposes its existing operational capabilities as one Main Sequence Harness Agent.
The agent is an additional project surface over the same reusable services used by the CLI and
FastAPI application; it is not a second API and it does not duplicate connector logic. Its project
tools are served by the API's MCP endpoint and run in the API as the person the Agent serves
([ADR 0015](adrs/0015_agent_tools_on_the_api_mcp_and_site_key_entry.md)).

## Repository Artifacts

- `AGENTS.md` defines the project boundary and safety rules.
- `.agents/agent_card.json` contains stable source metadata and six repository-owned skills.
- `.agents/skills/` explains the supported workflows and their invariants.
- `api/agent/main.py` is the Agent's entry point: the Tau runtime from `ms_tau_sdk.create_app()`.
- `src/alpaca_connectors/api/app/mcp_server.py` serves the structured project tools from
  `alpaca_connectors.agent_tools` at the API's `/mcp`.
- `.mainsequence/workflows/alpaca-connectors-api.yaml` declares the `alpaca` API with
  `mcp_enabled: true` and the `harness_agent` that uses it.
- `.agents/alpaca-command-center-icon.png` is the Alpaca artwork. The workflow no longer declares an
  Agent icon, because the platform dropped that field.

The platform builds the complete runtime A2A Agent Card at deployment. Concrete interfaces,
security declarations, protocol versions, and runtime capabilities are intentionally absent from
the repository source card.

## Tool Surface

The Agent lists the tools with `alpaca__list_tools` and calls one with `alpaca__call_tool`, naming
it in `tool` and passing its `arguments`. Read tools for project state and supported resources:

- `alpaca_project_state`
- `alpaca_query_assets`
- `alpaca_query_accounts`
- `alpaca_query_universes`
- `alpaca_query_bar_configurations`
- `alpaca_query_etf_signals`
- `alpaca_query_rebalance_configurations`
- `alpaca_query_etf_portfolios`
- `alpaca_get_job_run_status`

Sequential mutation tools for the same application workflows:

- `alpaca_register_assets`
- `alpaca_manage_account`
- `alpaca_account_key_request`
- `alpaca_manage_universe`
- `alpaca_manage_bar_configuration`
- `alpaca_manage_etf_signal`
- `alpaca_manage_rebalance_configuration`
- `alpaca_manage_etf_portfolio`

Each multi-operation tool uses an explicit operation discriminator and a strict JSON Schema. The
MCP server runs the existing application services inside the API's request identity: authorship
and ownership follow the person the Agent serves. Arguments are validated against each schema, and
errors never quote them.

## Operational Boundaries

- The Organization Environment is derived from the deployed CodeRepositoryBranch and is never a
  user or tool argument.
- No tool accepts Alpaca credential values. To register an account or replace its keys from keys
  the person holds, `alpaca_account_key_request` creates a pending request for that person and
  returns the Alpaca site link; the person types the keys on the site, which sends them to the API
  with the request UID, and the Agent reads the result with operation `status`. Registration from
  existing Main Sequence Secret names stays available through `alpaca_manage_account`.
- Account creation preserves the mandatory initial Asset registration and holdings capture.
- Universe extraction registers missing constituents in bulk, replaces the linked AssetCategory in
  bulk, and publishes one complete observed weight frame.
- Bars, ETF signal, and ETF portfolio updates submit their existing Main Sequence Jobs and return a
  JobRun UID. Large updates do not execute inline in the agent session.
- Destructive tool operations require exact `DELETE <uid>` confirmation.
- The agent cannot place trades, submit orders, execute arbitrary SQL or shell commands, or create
  unrelated platform releases.

## Deployment

`.mainsequence/workflows/alpaca-connectors-api.yaml` declares the branch's one `harness_agent`
on workflow API `2.3.0`, next to the `alpaca` API whose MCP it uses
(`mcp_applications: [{resource: alpaca}]`); declarations in the same file are applied first, so
the API is in place before the Agent registers it. Its `source_path`, `api/agent/main.py`, serves
the object
`ms_tau_sdk.create_app()` returns; the platform runs it from the ordinary CodeRepository image, so
`ms-tau-sdk` is a runtime dependency in `pyproject.toml` and the exported `requirements.txt`. The
workflow enables automatic deployment and accepts every pushed commit through
`automatic_redeployment.tag_regex: null`; it contains no image, branch, Environment, or UID.

The resource declares `acts_for_requester: true`. Only an Organization admin's push enables it;
the Agent then calls the platform with the ordinary permissions of the person whose request it
serves, for at most 24 hours after that request. The retired `code_repository_coding_agent` kind
and its `command_center_icon`, `cpu_limit`, and `memory_limit` fields are rejected by the
platform, so the workflow no longer declares an icon.

The project keeps Uvicorn on the `0.52` line with a non-exact `>=0.52.4,<0.53` project range;
`uv.lock` records the reproducible installed version.

Platform validation of the file succeeds only after the push that indexes `api/agent/main.py`.
Repository preparation and workflow validation do not alone prove a live Agent is ready. After a
push, verify the workflow action, exact-commit image chain, deployment run, Agent readiness, the
`alpaca__list_tools` result (the 17-tool catalog), and that the API release advertises its MCP
connection before relying on it.
