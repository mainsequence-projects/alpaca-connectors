# Project Brief

## Current Goal

Make `README.md` the actual project entrypoint by summarizing the repository's current
capabilities, operator surfaces, and workflow boundaries instead of mostly redirecting readers to
the deeper docs.

## Success Condition

- `README.md` explains the project's supported capabilities in plain terms before linking to
  detailed docs.
- `README.md` summarizes the supported CLI, API, job, and Command Center surfaces accurately.
- `README.md` preserves the repo boundary between Alpaca-owned logic under `src/` and ETF-owned
  logic under `etf_extraction/`.
- The deeper docs remain secondary references rather than the only useful entrypoint.

## Scope

In scope:

- Rewriting `README.md` to summarize the implemented project surface.
- Keeping the capability summary aligned with the current CLI/API/job/UI behavior already present
  in the repo.
- Recording the documentation milestone in the local project-state files when needed.

Out of scope unless requested:

- Changing Alpaca, ETF extraction, API, or DataNode runtime behavior.
- Rewriting the detailed workflow pages under `docs/`.
- Live platform execution of registration, holdings-category sync, or daily stock-bar updates.
