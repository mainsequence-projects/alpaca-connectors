#!/usr/bin/env bash
# Local development stack: MetaTables local runtime, FastAPI and the Command Center site.
#
#   scripts/dev_stack.sh [all|api|site]     (default: all)
#
# - MetaTables: uses the laptop's running local API, otherwise starts it with `metatables serve
#   --local`. Every checkout shares that one API (mainsequence-metatable 0.1.19).
# - API: the same settings as the VS Code "Debug Alpaca Connectors API (8321)" configuration,
#   connected to the local MetaTables API.
# - Site: the same settings as "Debug Alpaca Command Center Site (5421)".
#
# Ctrl-C (or stopping the preview) stops every process this script started; a MetaTables API that
# was already running is left alone. Used by .claude/launch.json.
set -euo pipefail

MODE="${1:-all}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE_DIR="${ALPACA_SITE_DIR:-$ROOT/../alpacaconnectorssite-ab9d29c9-2dcb-417f-8d88-ee9c9e9c8baf}"
API_PORT=8321
SITE_PORT=5421
PY="$ROOT/.venv/bin/python"
METATABLES="$ROOT/.venv/bin/metatables"

case "$MODE" in
  all | api | site) ;;
  *)
    echo "usage: scripts/dev_stack.sh [all|api|site]" >&2
    exit 2
    ;;
esac

cd "$ROOT"
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
export MAINSEQUENCE_AUTH_MODE=jwt

PIDS=()

log() { echo "[dev-stack] $*"; }
die() {
  echo "[dev-stack] $*" >&2
  exit 1
}
prefix() { awk -v tag="[$1] " '{ print tag $0; fflush() }'; }
port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
port_owner() { lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR == 2 { print $1 " (pid " $2 ")" }'; }

cleanup() {
  trap - EXIT INT TERM
  local pid
  for pid in "${PIDS[@]+"${PIDS[@]}"}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

require_free_port() {
  port_in_use "$1" || return 0
  die "$2 port $1 is already in use by $(port_owner "$1"). Stop it first; the API and site ports are fixed."
}

# Prints the running local MetaTables API's port; fails when no local API is running.
metatables_connection_port() {
  "$PY" - <<'PY' 2>/dev/null
import socket
import sys
from urllib.parse import urlsplit

from metatables import configure_local_client

try:
    target = urlsplit(configure_local_client())
    with socket.create_connection((target.hostname, target.port), timeout=1):
        pass
except Exception:
    sys.exit(1)
print(target.port)
PY
}

ensure_metatables() {
  local port
  if ! port="$(metatables_connection_port)"; then
    log "MetaTables: starting the local API"
    # The launcher stops its API worker and removes its connection file on SIGTERM.
    "$METATABLES" serve --local --no-open-browser > >(prefix metatables) 2>&1 &
    local launcher="$!" attempt
    for attempt in $(seq 1 60); do
      port="$(metatables_connection_port)" && break
      kill -0 "$launcher" 2>/dev/null || die "MetaTables exited before it was ready."
      sleep 1
    done
    [[ -n "$port" ]] || die "The local MetaTables API did not become ready."
    PIDS+=("$launcher")
  fi
  log "MetaTables: local API on 127.0.0.1:$port"
  report_local_schema
}

# Warns (never migrates) when the runtime or this project's schema needs a manual step.
report_local_schema() {
  "$PY" - <<'PY' 2>&1 | prefix metatables || true
from metatables import configure_local_client, get_runtime_status

configure_local_client()
state = get_runtime_status()
bootstrap = state.get("bootstrap") or {}
if state.get("local_mode") is not True:
    print("WARNING: the API is not in Local mode; restart it with metatables serve --local.")
elif bootstrap.get("migration_status") != "up_to_date" or not bootstrap.get("active"):
    print("WARNING: the local runtime is not initialized: metatables --local runtime initialize")
else:
    from alpaca_connectors.migrations.schema_gate import schema_state

    for provider in schema_state().providers:
        if provider.behind:
            print(
                f"WARNING: {provider.provider} is behind ({', '.join(provider.applied) or 'none'}"
                f" -> {provider.code_head}): metatables --local migrations upgrade "
                f"--provider {provider.provider} head"
            )
    print(f"local runtime ready: {(state.get('data_source') or {}).get('uid')}")
PY
}

start_api() {
  require_free_port "$API_PORT" "API"
  log "API: http://127.0.0.1:$API_PORT"
  PYTHONPATH="$ROOT" \
    ALPACA_CONNECTORS_CORS_ORIGINS="http://127.0.0.1:$SITE_PORT,http://localhost:$SITE_PORT" \
    "$PY" -c '
import sys

from metatables import configure_local_client

configure_local_client()
from uvicorn.main import main

main(sys.argv[1:], prog_name="uvicorn")
' api.app.main:app --host 127.0.0.1 --port "$API_PORT" --reload > >(prefix api) 2>&1 &
  PIDS+=("$!")
}

start_site() {
  local vite="$SITE_DIR/node_modules/vite/bin/vite.js"
  [[ -f "$vite" ]] || die "Vite is missing; run 'npm ci' in $SITE_DIR."
  command -v node >/dev/null || die "node is not on PATH."
  require_free_port "$SITE_PORT" "Site"
  log "Site: http://127.0.0.1:$SITE_PORT"
  (cd "$SITE_DIR" && VITE_API_BASE_URL="http://127.0.0.1:$API_PORT" exec node "$vite" \
    --host 127.0.0.1 --port "$SITE_PORT" --strictPort) > >(prefix site) 2>&1 &
  PIDS+=("$!")
}

case "$MODE" in
  all)
    ensure_metatables
    start_api
    start_site
    ;;
  api)
    ensure_metatables
    start_api
    ;;
  site) start_site ;;
esac

# Stop everything as soon as any started process exits.
while true; do
  for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null || die "A process exited (pid $pid); stopping the stack."
  done
  sleep 2
done
