#!/usr/bin/env bash
# Session replay twin: offline checks and demo runs (docs/SIM-TWIN.md). Loopback only, never touches hardware.
#   run.sh --check            py_compile + unit/contract/integration tests (< 10 s, no network; integration SKIPs without pymavlink)
#   run.sh run <scenario> [--seed N] [--speed X] [--json F]    one replay (~20 s real), table + JSON
#   run.sh matrix [--seeds 1,2] [--json F]                      scenarios x seeds
# Env: PY (python3 default; needs pymavlink for the integration part and for run/matrix). Exit: 0 pass, 1 fail, 77 skipped.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
export PY
export PYTHONDONTWRITEBYTECODE=1
command -v "$PY" >/dev/null 2>&1 || { echo "SKIP twin: $PY not found"; exit 77; }

mode="${1:---check}"
case "$mode" in
	--check)
		for f in "$HERE"/*.py; do
			"$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "FAIL twin: syntax $f"; exit 1; }
		done
		log="$(mktemp)"
		trap 'rm -f "$log"' EXIT
		if "$PY" "$HERE/test_twin.py" >"$log" 2>&1; then
			n="$(sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' "$log")"
			sk="$(sed -n 's/^OK (skipped=\([0-9]*\))$/\1/p' "$log")"
			echo "PASS twin: ${n:-?} tests, skipped=${sk:-0}"
		else
			cat "$log"
			echo "FAIL twin"
			exit 1
		fi
		;;
	run | matrix | plan | contracts)
		exec "$PY" "$HERE/twin.py" "$@"
		;;
	*)
		echo "usage: run.sh [--check | run <scenario> ... | matrix ... | plan <scenario> ... | contracts]" >&2
		exit 2
		;;
esac
