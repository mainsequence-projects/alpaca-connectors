# Development Workflow

## Setup

```bash
uv sync
mainsequence refresh-token
mainsequence code-repository current --debug --json
metatables serve --local          # in its own terminal; the laptop's single local runtime
metatables --local runtime status
metatables --local migrations upgrade --provider msm_migrations:migration head
metatables --local migrations upgrade --provider alpaca_connectors.migrations:migration head
```

Hosted environments are migrated only by the deployment workflow's migration Job
(`.mainsequence/workflows/alpaca-connectors-migrations.yaml`, ADR 0012). From a developer or agent
session, use only the read-only `metatables migrations current --provider
alpaca_connectors.migrations:migration` against a hosted environment. Every Job launcher checks the
applied schema first and skips the run while the database is behind its code.

`mainsequence refresh-token` renews the saved session; the SDK keeps credentials in the operating
system credential store, not in the CodeRepository `.env`. MetaTables schema commands come from the
`metatables` CLI installed by `mainsequence-metatable`.

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
uv run ruff format --check api src tests
uv run mkdocs build --strict
```

## Exercise The Backend

```bash
alpaca-connectors universe-source seed-defaults
alpaca-connectors universe-source list
alpaca-connectors account list
alpaca-connectors market-data dataset list
uv run uvicorn alpaca_connectors.api.app.main:app --reload
```

Mutating account and market-data operations use a registered Account UID. CLI account registration
accepts Main Sequence Secret names only; the API also accepts values to store as managed Secrets. Universe sync uses a UniverseSource UID and does not infer
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
