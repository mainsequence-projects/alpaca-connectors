# Alpaca Agent

The repository exposes its existing operational capabilities as one Main Sequence CodeRepository
Coding Agent. The agent is an additional project surface over the same reusable services used by
the CLI and FastAPI application; it is not a second API and it does not duplicate connector logic.

## Repository Artifacts

- `AGENTS.md` defines the project boundary and safety rules.
- `.agents/agent_card.json` contains stable source metadata and six repository-owned skills.
- `.agents/skills/` explains the supported workflows and their invariants.
- `.tau/extensions/alpaca_connectors/extension.py` registers structured project tools.
- `.mainsequence/workflows/alpaca-connectors-agent.yaml` declares automatic deployment.
- `.agents/alpaca-command-center-icon.png` is the full-color Command Center Agent icon. It is
  presentation metadata and is not part of the Agent Card or runtime identity.

The platform builds the complete runtime A2A Agent Card at deployment. Concrete interfaces,
security declarations, protocol versions, and runtime capabilities are intentionally absent from
the repository source card.

## Tool Surface

The Tau extension exposes read tools for project state and supported resources:

- `alpaca_project_state`
- `alpaca_query_assets`
- `alpaca_query_accounts`
- `alpaca_query_universes`
- `alpaca_query_bar_configurations`
- `alpaca_query_etf_signals`
- `alpaca_query_rebalance_configurations`
- `alpaca_query_etf_portfolios`
- `alpaca_get_job_run_status`

It exposes sequential mutation tools for the same application workflows:

- `alpaca_register_assets`
- `alpaca_manage_account`
- `alpaca_manage_universe`
- `alpaca_manage_bar_configuration`
- `alpaca_manage_etf_signal`
- `alpaca_manage_rebalance_configuration`
- `alpaca_manage_etf_portfolio`

Each multi-operation tool uses an explicit operation discriminator and a strict JSON Schema. The
adapter imports the existing application services directly; it never performs HTTP loopback to the
FastAPI application.

## Operational Boundaries

- The Organization Environment is derived from the deployed CodeRepositoryBranch and is never a
  user or tool argument.
- Account registration receives Main Sequence Secret names, never Alpaca credential values.
- Account creation preserves the mandatory initial Asset registration and holdings capture.
- Universe extraction registers missing constituents in bulk, replaces the linked AssetCategory in
  bulk, and publishes one complete observed weight frame.
- Bars, ETF signal, and ETF portfolio updates submit their existing Main Sequence Jobs and return a
  JobRun UID. Large updates do not execute inline in the agent session.
- Destructive tool operations require exact `DELETE <uid>` confirmation.
- The agent cannot place trades, submit orders, execute arbitrary SQL or shell commands, or create
  unrelated platform releases.

## Automatic Deployment And Icon

The API `2.3.0` workflow enables automatic deployment and accepts every synchronized commit through
`automatic_redeployment_policy.tag_regex: null`. The backend derives the exact CodeRepository image
and CodeRepository Executor image from the immutable repository event; the workflow contains no
image, branch, Environment, or harness selector.

The project keeps Uvicorn on the platform-executor-compatible `0.52` line with a non-exact
`>=0.52.4,<0.53` project range; `uv.lock` records the reproducible installed version.

The workflow declares:

```yaml
command_center_icon:
  path: .agents/alpaca-command-center-icon.png
  rendering: color
```

The file reuses the static site's Alpaca artwork, but `rendering: color` preserves its authored
pixels. This is distinct from the static-site navigation mask, which remains theme-aware and
monochrome after backend sanitization.

Repository preparation and workflow validation do not alone prove a live Agent is ready. After a
sync, verify the workflow action, exact-commit image chain, deployment run, Agent readiness, loaded
extension diagnostics, and the 16-tool catalog before relying on it.
