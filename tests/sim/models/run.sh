#!/usr/bin/env bash
# Calibratable-model checks (RF / power / latency + stochastic scenario engine): no network, no root, < 10 s.
#   run.sh --check          py_compile + unit/property/golden tests (the fast mode used by smoke.sh)
#   run.sh                  same, then print the demo tables (range sweep, power report, latency matrix)
#   run.sh --update-golden  rewrite golden/*.txt after an INTENDED model change
# Env: PY (python3 default). Exit: 0 pass, 1 fail, 77 skipped (no python3).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
command -v "$PY" >/dev/null 2>&1 || { echo "SKIP models: $PY not found"; exit 77; }
unset MODEL_PARAMS MODEL_MEASURED MODEL_SET

mode="${1:-}"
case "$mode" in
	""|--check|--update-golden) ;;
	*) echo "usage: run.sh [--check|--update-golden]" >&2; exit 2 ;;
esac

for f in "$HERE"/*.py; do
	"$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "FAIL models: syntax $f"; exit 1; }
done

if [ "$mode" = "--update-golden" ]; then
	UPDATE_GOLDEN=1 "$PY" "$HERE/test_models.py" >/dev/null 2>&1
	UPDATE_GOLDEN=1 "$PY" "$HERE/test_degrade.py" >/dev/null 2>&1
	"$PY" "$HERE/test_models.py" || exit 1
	"$PY" "$HERE/test_degrade.py" || exit 1
	echo "models: golden updated"
	exit 0
fi

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
if "$PY" "$HERE/test_models.py" >"$log" 2>&1 && "$PY" "$HERE/test_degrade.py" >>"$log" 2>&1; then
	n="$(sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' "$log" | awk '{s+=$1} END {print s}')"
	echo "PASS models: $n tests; $(grep -o 'UNMEASURED parameters: .*' "$log"); $(grep -o 'UNMEASURED parameters (degrade file): .*' "$log")"
else
	cat "$log"
	echo "FAIL models"
	exit 1
fi
if [ -z "$mode" ]; then
	"$PY" "$HERE/rf_model.py" sweep
	"$PY" "$HERE/power_model.py" report --board pi5 --psu-a 3 --with fc,webcam,fan
	"$PY" "$HERE/latency_budget.py" matrix --res 1920x1080 --bitrate 8000 --mcs 3
	"$PY" "$HERE/scenario_engine.py" run nominal_pi5_5a_150m --n 60
fi
exit 0
