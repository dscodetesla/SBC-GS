#!/usr/bin/env bash
# Virtual power lab (USB / Pi 5 / RTL8812): virtual instruments, bench/doctor.sh with shims, bench/ingest.py, overlay -> calib.py whatif. No hardware, no network, no root.
#   run.sh --check          py_compile + shellcheck (if installed) + unit/e2e tests (stdlib unittest), < 15 s
#   run.sh --demo [SCEN]    the pipeline on a scenario (default calib_pi5_hot_adapter) with the report, overlay and what-if printed
#   run.sh --sensitivity    channel sensitivity study through the models (about a minute): which measurement narrows the power model most
# Env: PY (python3 default). Exit: 0 pass, 1 fail, 77 skipped (no python3).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
command -v "$PY" >/dev/null 2>&1 || { echo "SKIP powerlab: $PY not found"; exit 77; }
unset MODEL_PARAMS MODEL_MEASURED MODEL_SET DEGRADE_PARAMS DEGRADE_MEASURED DEGRADE_SET

mode="${1:-}"
case "$mode" in
	--check)
		for f in "$HERE"/*.py "$REPO/bench/ingest.py"; do
			"$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "FAIL powerlab: syntax $f"; exit 1; }
		done
		for f in "$HERE"/*.sh "$HERE"/shims/* "$REPO/bench/doctor.sh"; do bash -n "$f" || { echo "FAIL powerlab: bash -n $f"; exit 1; }; done
		"$PY" -c 'import json,sys; [json.load(open(f)) for f in sys.argv[1:]]' "$REPO/bench/ingest-rules.json" "$HERE"/scenarios/*.json || { echo "FAIL powerlab: JSON"; exit 1; }
		log="$(mktemp)"; trap 'rm -f "$log"' EXIT
		if "$PY" "$HERE/test_powerlab.py" >"$log" 2>&1; then
			n="$(sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' "$log")"
			echo "PASS powerlab: $n tests (SYNTH data: tools tested, hardware not)"
			exit 0
		fi
		cat "$log"; echo "FAIL powerlab"; exit 1 ;;
	--demo)
		scn="${2:-calib_pi5_hot_adapter}"
		out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
		"$HERE/pipeline.sh" "$HERE/scenarios/$scn.json" "$out" --whatif nominal_pi5_5a_150m --n 100 || exit 1
		cat "$out/report.txt"; echo; echo "# truth (SYNTH) used by the generator:"; "$PY" -c 'import json,sys; [print("  %-44s %s" % kv) for kv in json.load(open(sys.argv[1]))["values"].items()]' "$out/truth.json"
		echo; echo "# calib.py whatif:"; cat "$out/whatif.txt"
		exit 0 ;;
	--sensitivity) shift; exec "$PY" "$HERE/sensitivity.py" "$@" ;;
	*) echo "usage: run.sh --check | --demo [SCENARIO] | --sensitivity" >&2; exit 2 ;;
esac
