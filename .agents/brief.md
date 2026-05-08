# Project Brief

## Current Goal

Prepare this repository for Main Sequence project-to-agent use through local project artifacts only:
real project-specific `AGENTS.md` instructions, project-specific custom skills, an agent card, and
docs/validation that describe the existing supported project surface accurately.

## Success Condition

- `AGENTS.md` contains real project-specific instructions and an explicit local project-to-agent
  boundary.
- `.agents/skills/` contains project-specific non-Main Sequence skills that match the real
  supported project workflows.
- `.agents/agent_card.json` exists and matches the local skill set plus project version.
- `README.md`, `docs/agent.md`, and local validation artifacts describe the agent-ready project
  surface without claiming unsupported runtime or platform behavior.

## Scope

In scope:

- Updating local project metadata and docs for project-to-agent readiness.
- Adding or rewriting project-specific custom skills under `.agents/skills/`.
- Creating the local agent card and validation artifacts.

Out of scope unless requested:

- Changing the supported Alpaca, ETF extraction, API, or DataNode business logic beyond what is
  needed to describe the local agent-ready surface accurately.
- Introducing a standalone local `agent.py` runtime.
- Claiming or verifying a live platform release path for the project-to-agent surface.
