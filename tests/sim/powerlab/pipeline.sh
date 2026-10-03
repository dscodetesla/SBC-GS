#!/usr/bin/env bash
# Whole bench-day pipeline on synthetic data (no hardware): scenario -> virtual instruments (gen.py) -> bench/doctor.sh with the
# vcgencmd/dmesg/lsusb/date shims -> bench/ingest.py -> overlay -> optionally tests/sim/models/calib.py whatif.
#   tests/sim/powerlab/pipeline.sh SCENARIO.json OUTDIR [--whatif MODEL_SCENARIO] [--n N] [-- extra ingest args]
# Writes OUTDIR/{doctor.json,report.txt,report.json,overlay.json,whatif.txt,...}. Everything is SYNTH: the numbers test the tools, they say nothing about hardware.
# Env: PY (python3 default). Exit: the status of the failing step, 0 when all steps pass (ingest --strict is NOT used here).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
[ $# -ge 2 ] || { sed -n '2,6p' "${BASH_SOURCE[0]}" >&2; exit 2; }
scn="$1" out="$2"; shift 2
whatif="" n_draws="" extra=()
while [ $# -gt 0 ]; do
	case "$1" in
		--whatif) whatif="$2"; shift 2 ;;
		--n) n_draws="$2"; shift 2 ;;
		--) shift; extra=("$@"); break ;;
		*) echo "pipeline: unknown argument $1" >&2; exit 2 ;;
	esac
done
mkdir -p "$out" || exit 1
"$PY" "$HERE/gen.py" "$scn" --out "$out" >"$out/gen.log" || { cat "$out/gen.log" >&2; exit 1; }
PATH="$HERE/shims:$PATH" POWERLAB_FIXTURE="$out" DOCTOR_ROOT="$out/sysroot" "$REPO/bench/doctor.sh" "$out/doctor.json" >"$out/doctor.log" 2>&1 || { cat "$out/doctor.log" >&2; exit 1; }
mapfile -t claims <"$out/ingest.args"
"$PY" "$REPO/bench/ingest.py" --doctor "$out/doctor.json" --instr-log "$out/pmic.log" \
	--meter "psu:$out/meter_psu.csv" --meter "dongle:$out/meter_dongle.csv" --windows "$out/windows.txt" --wfb-json "$out/wfb.jsonl" \
	--overlay "$out/overlay.json" --report-json "$out/report.json" "${claims[@]}" "${extra[@]}" >"$out/report.txt" 2>"$out/ingest.err"
rc=$?
[ "$rc" = 0 ] || { cat "$out/ingest.err" >&2; exit "$rc"; }
if [ -n "$whatif" ]; then
	"$PY" "$REPO/tests/sim/models/calib.py" whatif "$out/overlay.json" --scenario "$whatif" --n "${n_draws:-100}" >"$out/whatif.txt" 2>"$out/whatif.err" || { cat "$out/whatif.err" >&2; exit 1; }
fi
exit 0
