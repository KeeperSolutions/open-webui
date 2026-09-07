#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Flake check: run the FULL e2e suite (fresh backend + fresh scratch DB
# every time) N times and report pass/fail per iteration.
#
# This is the real flake test — each run is a clean backend, so it also
# catches state-leak / ordering bugs a same-process Cypress repeat hides.
#
# Usage:
#   CYPRESS_E2E_MODEL=qwen2.5:7b ./scripts/e2e-flake.sh          # 5 runs
#   CYPRESS_E2E_MODEL=qwen2.5:7b ./scripts/e2e-flake.sh 10       # 10 runs
#   CYPRESS_E2E_MODEL=qwen2.5:7b ./scripts/e2e-flake.sh 10 --spec cypress/e2e/02-chat-depth.cy.ts
#
# Extra args after the count are forwarded to `npm run e2e` (so to
# scripts/e2e.sh -> cypress run). Stops nothing on failure — runs all N
# so you see the flake rate, not just the first red.
#
# By default each run is quiet: a heartbeat line ("… 45s — ✓ ...") ticks
# every 15s so you know it's alive and which step it's on, the full log
# is saved and its path printed, and the Cypress summary + PASS/FAIL are
# shown when the run ends. E2E_FLAKE_VERBOSE=1 streams the full output.
# ---------------------------------------------------------------------------
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "${CYPRESS_E2E_MODEL:-}" ]]; then
	echo "error: CYPRESS_E2E_MODEL is required." >&2
	exit 1
fi

RUNS="${1:-5}"
if [[ "$RUNS" =~ ^[0-9]+$ ]]; then
	shift || true
else
	RUNS=5
fi

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/owui-e2e-flake.XXXXXX")"
echo "==> $RUNS runs; per-run logs in $LOG_DIR"
echo "==> forwarding to npm run e2e: ${*:-<none>}"
echo

declare -a results=()
pass=0
fail=0
start=$(date +%s)

# VERBOSE=1 -> stream each run's full output to the terminal too.
# default    -> quiet: a heartbeat line ticks while the run works, the
#               full log is saved and linked, and we print the Cypress
#               summary + PASS/FAIL when the run finishes.
VERBOSE="${E2E_FLAKE_VERBOSE:-}"

# Print a "still working" tick every ~15s for the PID passed in, showing
# the last it()-ish line seen in the log so you know which step it's on.
heartbeat() {
	local pid="$1" logf="$2" secs=0 last=""
	while kill -0 "$pid" 2>/dev/null; do
		sleep 5
		secs=$((secs + 5))
		if (( secs % 15 == 0 )); then
			# last "✓/✗ ..." or "N passing" or a spec header from the log
			last="$(grep -aE '(✓|✔|✗|✖|[0-9]+ (passing|failing)|Running:)' "$logf" 2>/dev/null | tail -1 | sed 's/^[[:space:]]*//' | cut -c1-80)"
			printf '  … %ds%s\n' "$secs" "${last:+  — $last}"
		fi
	done
}

for ((i = 1; i <= RUNS; i++)); do
	printf '\n=== run %d/%d ===\n' "$i" "$RUNS"
	run_start=$(date +%s)
	log="$LOG_DIR/run-$(printf '%02d' "$i").log"
	echo "    log: $log"
	rc=0

	if [[ "$VERBOSE" == "1" ]]; then
		set -o pipefail
		CYPRESS_E2E_MODEL="$CYPRESS_E2E_MODEL" npm run e2e -- "$@" 2>&1 \
			| tee "$log" | sed 's/^/  | /' || rc=$?
		set +o pipefail
	else
		CYPRESS_E2E_MODEL="$CYPRESS_E2E_MODEL" npm run e2e -- "$@" >"$log" 2>&1 &
		run_pid=$!
		heartbeat "$run_pid" "$log" &
		hb_pid=$!
		wait "$run_pid" || rc=$?
		kill "$hb_pid" 2>/dev/null || true
		wait "$hb_pid" 2>/dev/null || true
	fi

	if [[ "$rc" -eq 0 ]]; then
		status="PASS"
		pass=$((pass + 1))
	else
		status="FAIL"
		fail=$((fail + 1))
	fi

	run_end=$(date +%s)
	dur=$((run_end - run_start))

	# Pull the Cypress summary line(s) for a quick per-run readout.
	summary="$(grep -E '^\s+(✔|✖)\s+[0-9]{2}-.*\.cy\.ts' "$log" | sed 's/^/      /')"

	printf -- '=== run %d/%d -> %s (%ds)\n' "$i" "$RUNS" "$status" "$dur"
	[[ -n "$summary" ]] && echo "$summary"
	results+=("run $(printf '%02d' "$i"): $status (${dur}s) — $log")
done

end=$(date +%s)
echo
echo "======================================================================"
printf 'FLAKE CHECK: %d/%d passed, %d failed   (%ds total)\n' "$pass" "$RUNS" "$fail" "$((end - start))"
echo "----------------------------------------------------------------------"
printf '%s\n' "${results[@]}"
echo "======================================================================"

if [[ "$fail" -gt 0 ]]; then
	echo "Flaky or broken. Inspect the FAIL logs above."
	exit 1
fi
echo "All green across $RUNS runs."
