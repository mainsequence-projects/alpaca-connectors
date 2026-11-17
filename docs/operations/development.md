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
exact dependency versions; `uv.lock` is the reproducible resolution.

The release image is built from `requirements.txt` alone with plain pip (`pip wheel --requirement`
then `pip check`), which ignores `[tool.uv] override-dependencies`. Never use an override to get
past a dependency's pin, and regenerate `requirements.txt`
(`uv export --locked --no-dev --no-hashes --format requirements-txt -o requirements.txt`) with every
`uv.lock` change, or the release build fails.

The rules for running the CLI, the API and the Jobs against an environment are in the
[Operator Guide](operator-guide.md).

## Verify

```bash
uv run pytest
uv run ruff check api src tests
uv run ruff format --check api src tests
uv run mkdocs build --strict
```

### Writes on PostgreSQL

Hosted MetaTables runs PostgreSQL, so `tests/test_postgresql_json_columns.py` runs the
connector's operation writes against a real PostgreSQL. It binds each compiled statement with
the MetaTables PostgreSQL backend's own `parameters_for`, the step that refused the JSON list in
issue #27. The tests are skipped unless `ALPACA_CONNECTORS_TEST_POSTGRES_URL` names a disposable
`alpaca_*` database on localhost, and they drop and recreate its `public` schema:

```bash
docker run -d --rm --name alpaca-connectors-pg-test -e POSTGRES_PASSWORD=alpaca \
  -e POSTGRES_DB=alpaca_test -p 127.0.0.1:5448:5432 postgres:16-alpine
ALPACA_CONNECTORS_TEST_POSTGRES_URL=postgresql://postgres:alpaca@127.0.0.1:5448/alpaca_test \
  uv run pytest tests/test_postgresql_json_columns.py
```

The project requires MetaTables 0.1.66 or later: tables in several namespaces
([ADR 0025](../adrs/0025_markets_reads_the_instances_through_a_second_namespace.md)), and since
0.1.65 JSON parameters typed as `json` (MetaTables#111). On an earlier client these tests fail with the hosted error,
`cannot adapt type 'dict' using placeholder '%s'`: a list was bound as a PostgreSQL array.

## Live End-To-End Test

`tests/test_live_account_lifecycle.py` runs the whole account life cycle on a real Alpaca paper
account and a local stack ([ADR 0023](../adrs/0023_account_sync_sizes_and_allocates_funds.md)).
It is skipped unless `ALPACA_E2E=1`:

```bash
ALPACA_E2E=1 .venv/bin/python -m pytest tests/test_live_account_lifecycle.py -x -v
```

| Stage | What it checks |
| --- | --- |
| Register | The account is created with the paper keys of `.env`; no key value comes back |
| Sync, no target | Only the capture runs; the two other steps are skipped |
| Two fixed-weight portfolios | AAPL and NVDA, and JPM and NVDA, published as weights rows |
| Exposure | A share of NAV in the first portfolio and an amount in the second |
| Sync | Each exposure is sized into units; an account holding only cash has full deltas |
| Rebalance | The deltas are traded; the fills are allocated and NVDA is split between the funds. With the market closed the orders are queued, checked as accepted and cancelled |
| Change and repeat | A portfolio with no weights fails the sizing; new weights and a lower exposure are sized again and traded |
| Tear down | The target is cleared and the account is deleted; positions the test bought are left |

It needs, on this machine:

- the local MetaTables runtime (`metatables serve --local`) with both migrations applied;
- the Markets API on ms-markets 3.7 or later, started from an ms-markets checkout against that
  runtime, with `MARKETS_API_URL` set for this API and for the test;
- this API at `http://127.0.0.1:8321` (`ALPACA_E2E_API_URL` names another address);
- a signed-in `mainsequence` CLI, for the Bearer token;
- `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY` in `.env`, which Git ignores.

A branch with no registered Environment cannot read the `ALPACA_CONNECTORS_DATA_KEY` Secret. For
such a branch, start the API with a local keyring instead: put one in `.env` as
`ALPACA_E2E_LOCAL_KEYRING` (`metatables.encryption.Keyring.new().render()`) and hand it to
MetaTables before uvicorn starts, without `--reload`:

```bash
set -a; source .env; set +a
MAINSEQUENCE_AUTH_MODE=jwt MARKETS_API_URL=http://127.0.0.1:8322 PYTHONPATH=. .venv/bin/python -c '
import os, sys
from metatables import configure_local_client
from metatables.encryption import set_keyring_source
configure_local_client()
ring = os.environ["ALPACA_E2E_LOCAL_KEYRING"]
set_keyring_source(lambda name: ring)
from uvicorn.main import main
main(sys.argv[1:], prog_name="uvicorn")
' api.app.main:app --host 127.0.0.1 --port 8321
```

What it does about the market and about leftovers:

- **Market closed.** Orders are queued for the next session, checked as accepted and cancelled
  again; the checks that need fills are left out.
- **Orders it queued.** The API places orders at Alpaca before it records the batch, so the
  test always cancels its own open orders at Alpaca, whether or not a check failed.
- **An earlier run.** It removes the account an interrupted run left and clears that account's
  target in Markets, which outlives the connector's registration.

Results on the local SQLite runtime with ms-markets 3.7.1, on 2026-11-15 with the market closed:

- **All ten stages pass.** Orders were queued, checked as accepted and cancelled; no order
  filled, so the allocation after fills is still not exercised.
- **ms-markets 3.7.0 could not record an order batch on SQLite**
  ([MainSequenceMarkets#73](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/73));
  3.7.1 fixes it.
- **A short position.** A fund that targets an asset long is assigned units even when the
  account is short that asset; the direct line takes the difference. That is ms-markets'
  documented rule (its ADR 0053, rule 4), so the test checks only that an asset's lines add up
  to what the account holds.
- **An order Alpaca refuses** is recorded as `rejected` with Alpaca's reason and reported as a
  warning, not a failure. The paper account is short AAPL, so Alpaca refuses a buy larger than
  the short.

It leaves the three registered assets, the three `ALPACA_E2E_*` portfolios, their calendar and
their weights rows in place; they are reused by the next run.

## Exercise The Backend

```bash
alpaca-connectors account list
alpaca-connectors market-data dataset list
alpaca-connectors portfolio list
uv run uvicorn alpaca_connectors.api.app.main:app --reload
```

Mutating account and market-data operations use a registered Account UID. CLI account registration
accepts Main Sequence Secret names only, imported once; the API also accepts values. Both store the
keys encrypted on the account row, which needs the `ALPACA_CONNECTORS_DATA_KEY` Secret
([operator guide](operator-guide.md#account-key-secret)). A portfolio is created only through the API, by
the application that gives it weights ([Portfolios](../portfolios/portfolios.md)).

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
