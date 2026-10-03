#!/usr/bin/env bash
# Mutation check of the power-lab tests, on a COPY of the files they use (never in place): every mutation must make test_powerlab.py FAIL.
# Prints one line per mutation and a summary; exit 0 only if all were killed.
#   tests/sim/powerlab/mutate.sh            all mutations (about 1-2 min)
#   tests/sim/powerlab/mutate.sh M2 M5      only the named ones
# Env: PY (python3 default).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1
unset MODEL_PARAMS MODEL_MEASURED MODEL_SET DEGRADE_PARAMS DEGRADE_MEASURED DEGRADE_SET
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
want=("$@")

# id name|file (relative to the repo root)|python expression that edits `s` (the file text); it must change the text
MUTS=(
"M1 get_throttled bit positions shifted (undervoltage read from bit 1)|bench/ingest-rules.json|s=s.replace('\"uv_now\": 0','\"uv_now\": 1')"
"M2 overlay entries lose their source|bench/ingest.py|s=s.replace('e = {\"value\": sig(value), \"source\": \"%s @%s\" % (source, iso(t if t is not None else self.now)),','e = {\"value\": sig(value), \"source\": \"\",')"
"M3 overlay entries lose the measurement time|bench/ingest.py|s=s.replace('\"measured_utc\": iso(t if t is not None else self.now), \"tag\": tag}','\"measured_utc\": \"\", \"tag\": tag}')"
"M4 undervoltage under a 5 A claim is not reported as a contradiction|bench/ingest.py|s=s.replace('if a.claim_psu_a is not None and ev:','if False and ev:')"
"M5 current unit mA read as A (scale 1 instead of 0.001)|bench/ingest-rules.json|s=s.replace('\"mA\": 0.001,','\"mA\": 1.0,',1)"
"M6 resistance sign flipped (meter V and PMIC V swapped)|bench/ingest.py|s=s.replace('(i, vp - v5) for _w, i, vp, v5 in pts','(i, v5 - vp) for _w, i, vp, v5 in pts')"
"M7 kernel 'Voltage normalised' (British spelling) no longer matched|bench/ingest-rules.json|s=s.replace('[Vv]oltage normali[sz]ed','[Vv]oltage normalized')"
"M8 doctor.sh no longer reports the dmesg exit status (field renamed)|bench/doctor.sh|s=s.replace('field dmesg_rc;','field dmesg_rc_x;')"
"M9 generator re-enumerates before the disconnect (event order broken)|tests/sim/powerlab/gen.py|s=s.replace('self.kmsg(t, \"usb %s: USB disconnect, device number %d\" % (d[\"bus_port\"], d[\"devnum\"]))\\n        d[\"up\"], d[\"until\"] = False, until','d[\"up\"], d[\"until\"] = False, until')"
"M10 samples while the radio was down count as device current|bench/ingest.py|s=s.replace(' for a, b in down)]',' for a, b in [])]')"
"M11 kernel sticky-bit clearing ignored by the generator|tests/sim/powerlab/gen.py|s=s.replace('                    if clears:\\n                        sticky_uv, sticky_soft = uv_now, soft_now','                    if False:\\n                        sticky_uv, sticky_soft = uv_now, soft_now')"
"M12 re-enumeration pairs from undervoltage drops are accepted as usb_reenum_s|bench/ingest.py|s=s.replace('(cause == \"overcurrent\" or c.a.reenum_all)','True')"
"M13 hard-budget check (K8) never fires|bench/ingest.py|s=s.replace('if hi > budget and not acted:','if False and hi > budget and not acted:')"
"M14 the generator ignores the truth cable resistance|tests/sim/powerlab/gen.py|s=s.replace('), g(\"power.cable_resistance_ohm\")','), 0.15')"
"M15 doctor.sh starts loading a module (not read-only; a no-op in the copy)|bench/doctor.sh|s=s.replace('set -u\\nroot=','set -u\\n: modprobe dummy\\nroot=')"
)
fail=0
for m in "${MUTS[@]}"; do
	id="${m%% *}"
	if [ ${#want[@]} -gt 0 ]; then
		hit=0; for w in "${want[@]}"; do [ "$w" = "$id" ] && hit=1; done
		[ "$hit" = 1 ] || continue
	fi
	rest="${m#* }"; name="${rest%%|*}"; r2="${rest#*|}"; file="${r2%%|*}"; expr="${r2#*|}"
	rm -rf "$tmp/c"; mkdir -p "$tmp/c/tests/sim" "$tmp/c/tests/static" "$tmp/c/docs"
	cp -r "$REPO/bench" "$tmp/c/bench"; cp -r "$HERE" "$tmp/c/tests/sim/powerlab"; cp -r "$REPO/tests/sim/models" "$tmp/c/tests/sim/models"
	cp "$REPO/tests/static/config_scan.py" "$tmp/c/tests/static/"; cp "$REPO/docs/SIM-POWERLAB.md" "$tmp/c/docs/" 2>/dev/null
	find "$tmp/c" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
	if ! "$PY" - "$tmp/c/$file" "$expr" <<'PY'
import sys
p, expr = sys.argv[1], sys.argv[2]
s = open(p).read()
o = s
exec(expr)
if s == o:
    sys.exit("mutation did not change the file: " + p)
open(p, "w").write(s)
PY
	then
		echo "ERROR $id: mutation not applied ($name)"; fail=1; continue
	fi
	if (cd "$tmp/c/tests/sim/powerlab" && "$PY" test_powerlab.py >"$tmp/out.log" 2>&1); then
		echo "SURVIVED $id: $name"; fail=1
	else
		echo "KILLED   $id: $name"
	fi
done
[ "$fail" = 0 ] && echo "mutation check: all mutations killed" || echo "mutation check: SURVIVORS or errors"
exit "$fail"
