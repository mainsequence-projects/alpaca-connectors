# Development Workflow

## Setup

```bash
uv sync
mainsequence code-repository refresh-token --path .
mainsequence code-repository current --debug --json
mainsequence migrations current --provider src.migrations:migration
mainsequence migrations upgrade --provider src.migrations:migration head
```

Python 3.13 is required. `pyproject.toml` declares compatible ranges and intentionally does not pin
exact dependency versions; `uv.lock` is the reproducible resolution.

Install Playwright support when a provider extraction path requires it:

```bash
bash scripts/install_browser_runtime.sh
```

## Verify

```bash
uv run pytest
uv run ruff check api src tests
uv run ruff format --check api src tests --exclude src/.agents
uv run mkdocs build --strict
```

## Exercise The Backend

```bash
alpaca-connectors universe-source seed-defaults
alpaca-connectors universe-source list
alpaca-connectors account list
alpaca-connectors market-data dataset list
uv run uvicorn api.app.main:app --reload
```

Mutating account and market-data operations use a registered Account UID. Account registration
accepts Main Sequence Secret names only. Universe sync uses a UniverseSource UID and does not infer
extraction targets from source-code constants.

## Debug the API and Command Center site

Open this backend repository as the VS Code workspace, select **Run and Debug**, and launch
**Debug Alpaca API + Command Center Site**. The compound configuration starts:

- FastAPI under the Python debugger at `http://127.0.0.1:8321`;
- the sibling Vite application at `http://127.0.0.1:5421`;
- Chrome with the JavaScript debugger after Vite is ready.

The API process loads the repository `.env`, uses JWT authentication mode, and permits CORS only
from the selected Vite origin. Vite receives `VITE_API_BASE_URL=http://127.0.0.1:8321` through the
launch environment. Both servers bind to exact ports; if another process takes either port, stop it
or select another free pair in `.vscode/launch.json` rather than allowing an automatic port change.

The sibling static-site repository and its `node_modules` installation must exist before launching
the compound. Run `npm ci` from that repository if the Vite executable is missing.
