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

Hosted environments are migrated only by the migration Job in
`.mainsequence/workflows/alpaca-connectors-api.yaml` (ADR 0012), which runs from the candidate image
before the API deploys. From a developer or agent
session, use only the read-only `metatables migrations current --provider
alpaca_connectors.migrations:migration` against a hosted environment. Every Job launcher checks the
applied schema first and skips the run while the database is behind its code.

Every entrypoint reaches Markets registries through the Markets API (ms-markets ADR 0047), so a
local process needs one: run the Markets API from an ms-markets checkout against the same local
runtime and point this project at it with `MARKETS_API_URL` (plus `MARKETS_API_TOKEN` when it
checks one). This project's own tables, including its time-series instances, stay on the local
MetaTables runtime.

`mainsequence refresh-token` renews the saved session; the SDK keeps credentials in the operating
system credential store, not in the CodeRepository `.env`. MetaTables schema commands come from the
`metatables` CLI installed by `mainsequence-metatable`.

Python 3.13 is required. `pyproject.toml` declares compatible ranges and intentionally does not pin
exact dependency versions; `uv.lock` is the reproducible resolution. The one exception is the
`etfhextractor` Git source, which `[tool.uv.sources]` pins to its release tag (`v0.4.4`).

Install Playwright support when a provider extraction path requires it:

```bash
bash scripts/install_browser_runtime.sh
```

The rules for running the CLI, the API and the Jobs against an environment are in the
[Operator Guide](operator-guide.md).

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
accepts Main Sequence Secret names only, imported once; the API also accepts values. Both store the
keys encrypted on the account row, which needs the `ALPACA_CONNECTORS_DATA_KEY` Secret
([operator guide](operator-guide.md#account-key-secret)). Universe sync uses a UniverseSource UID and does not infer
extraction targets from source-code constants.

### Call the local API

Every request to the API needs a caller ([ADR 0014](../adrs/0014_sdk_request_identity.md)).
Without deployment markers the API checks a direct Bearer token against
`MAINSEQUENCE_ENDPOINT/api/v1/users/me/`, so the CLI that issues the token and the API must use the
same backend. Both read `MAINSEQUENCE_ENDPOINT` from the environment; the repository `.env` holds the
development value:

```bash
set -a; source .env; set +a
curl -H "Authorization: Bearer $(.venv/bin/mainsequence auth token)" http://127.0.0.1:8321/health
```

A request without a token, or with a token that backend did not issue, gets 401; the API answers 503
when the backend cannot be reached. `/health`, `/openapi.json` and `/docs` need a token too. Tokens
are short-lived: request a new one when calls start answering 401.

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

The site calls the API directly from the browser and sends no token, so the API answers its
requests with 401 until the site sends `Authorization: Bearer <token>` from
`mainsequence auth token`. That change belongs to the site repository. The API's CORS policy wraps
the request identity, so the site can read those 401 responses.

## Run the local stack without a debugger

`scripts/dev_stack.sh` starts the same API and site, with the same ports and environment, and
connects the API to the local MetaTables runtime:

```bash
scripts/dev_stack.sh          # MetaTables, API and site
scripts/dev_stack.sh api      # MetaTables and API
scripts/dev_stack.sh site     # site only
```

The script connects through `metatables.configure_local_client()` to the laptop's one local
MetaTables API, which every checkout shares, and starts it with `metatables serve --local` when it is
not running. The API and site ports are fixed: when one is taken, the script stops and names the
owning process. It warns, and never migrates, when the local runtime is uninitialized or a
provider's schema is behind. Ctrl-C stops everything it started and leaves a MetaTables API that was
already running alone.

`.claude/launch.json` exposes the three modes to the Claude desktop app's Browser panel.
