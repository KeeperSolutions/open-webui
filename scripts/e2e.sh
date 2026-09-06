#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Run the Cypress E2E suite against the DEV SERVER + a THROWAWAY sqlite DB.
#
# No `npm run build` needed — this runs the Vite dev server (port 5173) so
# the frontend is always in sync with `src/`. It also spins up the backend
# (port 8080) on an isolated scratch DATA_DIR + DATABASE_URL, runs
# `cypress run` against http://localhost:5173, then ALWAYS tears both
# processes down and deletes the scratch dir — even on failure or Ctrl-C.
#
# Why the dev server and not the build:
#   the built (SSG) output can silently go stale if you forget to rebuild
#   after a frontend change, and then the suite tests old code. The dev
#   server has no such footgun. (The build-serving path is still what
#   production runs; if you specifically want to smoke-test that, build
#   first and point Cypress at :8080 by hand — see cypress/README.md.)
#
# Prereqs:
#   - Ollama running with the target model pulled
#   - CYPRESS_E2E_MODEL set (the suite refuses to run without it)
#   - deps installed (`npm install`, backend venv)
#
# Usage:
#   CYPRESS_E2E_MODEL=gemma3:1b ./scripts/e2e.sh
#   CYPRESS_E2E_MODEL=gemma3:1b ./scripts/e2e.sh --spec cypress/e2e/core-flow.cy.ts
#   E2E_FRONTEND_PORT=5199 CYPRESS_E2E_MODEL=gemma3:1b ./scripts/e2e.sh
#
# Env:
#   CYPRESS_E2E_MODEL    required — model id/tag you have pulled in Ollama
#   E2E_BACKEND_PORT     backend port  (default 8080 — the frontend's dev
#                        build hardcodes the API base to :8080, so changing
#                        this also needs a matching frontend change; leave it)
#   E2E_FRONTEND_PORT    Vite dev server port (default 5173)
#   E2E_KEEP_DB          set to 1 to skip deleting the scratch dir (debugging)
#   any extra args       forwarded to `cypress run`
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "${CYPRESS_E2E_MODEL:-}" ]]; then
	echo "error: CYPRESS_E2E_MODEL is required (a model you have pulled in Ollama)." >&2
	echo "       e.g. CYPRESS_E2E_MODEL=gemma3:1b ./scripts/e2e.sh" >&2
	exit 1
fi

BACKEND_PORT="${E2E_BACKEND_PORT:-8080}"
FRONTEND_PORT="${E2E_FRONTEND_PORT:-5173}"
BACKEND_URL="http://localhost:${BACKEND_PORT}"
FRONTEND_URL="http://localhost:${FRONTEND_PORT}"

# Isolated scratch data dir — its own e2e-webui.db, uploads, vector store, etc.
SCRATCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/owui-e2e.XXXXXX")"
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
	local ec=$?
	for pid in "$FRONTEND_PID" "$BACKEND_PID"; do
		if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
			echo "==> stopping pid $pid"
			# kill the whole process group (Vite / uvicorn spawn children)
			kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
			wait "$pid" 2>/dev/null || true
		fi
	done
	if [[ "${E2E_KEEP_DB:-}" == "1" ]]; then
		echo "==> E2E_KEEP_DB=1 — leaving scratch dir: $SCRATCH_DIR"
	else
		echo "==> removing scratch dir: $SCRATCH_DIR"
		rm -rf "$SCRATCH_DIR"
	fi
	exit "$ec"
}
trap cleanup EXIT INT TERM

echo "==> scratch DATA_DIR:  $SCRATCH_DIR"
echo "==> backend:           $BACKEND_URL"
echo "==> frontend (Vite):   $FRONTEND_URL"
echo "==> model under test:  $CYPRESS_E2E_MODEL"

# --- backend -------------------------------------------------------------
# Source backend/.env for shared config (encryption key, Ollama URL,
# CREDITS_PER_EUR_CENT, ...) then FORCE the DB + data dir to the scratch
# location (backend/.env pins DATABASE_URL=sqlite:///./data/webui.db, so it
# must be overridden AFTER the source — which is why this isn't backend/dev.sh).
(
	cd "$REPO_ROOT/backend"
	set -m   # own process group, so cleanup can signal the whole tree
	set -a
	# shellcheck disable=SC1091
	[[ -f .env ]] && source .env
	set +a

	export DATA_DIR="$SCRATCH_DIR"
	# Prefixed filename so it can never be mistaken for the dev DB
	# (webui.db) in a stray `sqlite3` / editor tab.
	export DATABASE_URL="sqlite:///$SCRATCH_DIR/e2e-webui.db"
	export ENABLE_SIGNUP=true
	export ENABLE_LOGIN_FORM=true
	# dev mode: the frontend calls the API at :8080 from the :5173 origin.
	export CORS_ALLOW_ORIGIN="$FRONTEND_URL;$BACKEND_URL"
	export PYTHONPATH="$REPO_ROOT/backend:${PYTHONPATH:-}"

	# Prefer the project venv (the machine's default `python` is often an
	# unrelated venv); fall back to whatever `python` is active.
	if [[ -x "$REPO_ROOT/backend/venv/bin/python" ]]; then
		PY="$REPO_ROOT/backend/venv/bin/python"
	elif [[ -x "$REPO_ROOT/venv/bin/python" ]]; then
		PY="$REPO_ROOT/venv/bin/python"
	else
		PY="$(command -v python3 || command -v python)"
	fi

	# No --reload: fixed run, and the reloader complicates teardown.
	exec "$PY" -m uvicorn open_webui.main:app --host 0.0.0.0 --port "$BACKEND_PORT" \
		--forwarded-allow-ips '*' --timeout-graceful-shutdown 5
) &
BACKEND_PID=$!

# --- frontend (Vite dev server) ----------------------------------------
(
	set -m
	exec npm run dev -- --port "$FRONTEND_PORT" --strictPort
) &
FRONTEND_PID=$!

# --- wait for both to be serving --------------------------------------
wait_for() {
	local name="$1" url="$2" pid="$3" limit="$4"
	echo -n "==> waiting for $name"
	for ((i = 1; i <= limit; i++)); do
		if curl -sf -o /dev/null "$url"; then
			echo " — up"
			return 0
		fi
		if ! kill -0 "$pid" 2>/dev/null; then
			echo
			echo "error: $name exited during startup" >&2
			return 1
		fi
		echo -n "."
		sleep 1
	done
	echo
	echo "error: $name did not become ready within ${limit}s" >&2
	return 1
}

# backend runs DB migrations on boot — give it room
wait_for "backend"  "$BACKEND_URL/health"  "$BACKEND_PID"  90
# first `npm run dev` also runs pyodide:fetch (cached after the first time)
wait_for "frontend" "$FRONTEND_URL"        "$FRONTEND_PID" 120

echo "==> running cypress"
CYPRESS_BASE_URL="$FRONTEND_URL" npx cypress run "$@"
# cleanup() runs on EXIT
