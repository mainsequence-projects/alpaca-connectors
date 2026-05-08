# Agentic Capabilities

## Goal

This project is prepared for Main Sequence project-to-agent workflows through local project
instructions, project-specific skills, and an agent card that describe the existing supported
repository surface.

This page documents the local agent-ready artifacts only.

It does not claim a live platform release path or a standalone local `agent.py` runtime.

## Local Agent Artifacts

- `AGENTS.md`
- `.agents/skills/`
- `.agents/agent_card.json`

These files describe how an agent should understand and operate on the repository's existing
capabilities.

## Supported Agent Task Boundary

The local agent-ready surface is intentionally limited to workflows already implemented by the
repository:

- strict Alpaca asset registration
- ETF holdings-category planning and sync
- Alpaca stock-bar planning and update workflows
- the existing thin FastAPI support surface

The project does not claim unsupported local agent actions such as:

- trading
- portfolio management
- arbitrary platform release creation
- runtime behaviors that are not represented by the current CLI, services, API, or docs

## Skill Mapping

Project-specific non-Main Sequence skills live under `.agents/skills/` and currently cover:

- `asset_registration`
- `holdings_category`
- `stock_bars`
- `application_surfaces/api_surfaces`

These skills are meant to describe and support the existing project workflows rather than invent
new ones.

## Agent Card Notes

The agent card lives at:

- `.agents/agent_card.json`

The card currently uses the scaffold template structure required by the project-to-agent skill.

Because this repository does not yet declare a concrete local agent runtime endpoint, the interface
and provider URLs in the card should be treated as template placeholders until the deployment model
is explicitly chosen and verified.
