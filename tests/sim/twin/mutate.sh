#!/usr/bin/env bash
# Mutation check of the twin tests, on a COPY of the needed parts of the repo (never in place): every mutation must make
# `tests/sim/twin/run.sh --check` FAIL. One line per mutation and a summary; exit 0 only if all were killed.
#   tests/sim/twin/mutate.sh            all mutations (about 1-2 min with 3 parallel jobs)
#   tests/sim/twin/mutate.sh M1 M7      only the named ones
# Env: PY (python3 default, needs pymavlink: without it the integration tests SKIP and some mutations would survive), JOBS (default 3).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PY="${PY:-python3}"
JOBS="${JOBS:-3}"
export PYTHONDONTWRITEBYTECODE=1 PY
export TWIN_NO_VETH=1   # the live air_relay.py veth cross-check would collide between parallel mutants (it is part of the normal --check as root)
unset MODEL_PARAMS MODEL_MEASURED MODEL_SET DEGRADE_PARAMS DEGRADE_MEASURED DEGRADE_SET
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
want=("$@")

# id|file (relative to the copy root)|python statement(s) editing `s` (the file text); the text must change
MUTS=(
"M1 dead-man disabled in tx12_bridge.py|bench/tx12_bridge.py|s=s.replace('elif state == \"ACTIVE\" and not fresh:','elif state == \"ACTIVE\" and not fresh and False:')"
"M2 no throttle-failsafe frames before release|bench/tx12_bridge.py|s=s.replace('tx(throttle_fs_frame() if rel_n < THROTTLE_FS_FRAMES else [RELEASE] * NCH)','tx([RELEASE] * NCH)')"
"M3 bridge never goes silent after the release hold|bench/tx12_bridge.py|s=s.replace('state = \"SILENT\"','state = \"RELEASE\"; t_rel = now')"
"M4 bridge sends with the wrong sysid|bench/tx12_bridge.py|s=s.replace('source_system=a.sysid, source_component=COMPONENT_ID','source_system=a.sysid + 1, source_component=COMPONENT_ID')"
"M5 FC model: override never expires|tests/sim/apm_model.py|s=s.replace('if self.rc_override_time < 0 or t - o[1] <= self.rc_override_time:','if True:')"
"M6 FC model: GCS failsafe never fires|tests/sim/apm_model.py|s=s.replace('if (self.fs_gcs_enable and self.last_gcs is not None','if (False and self.last_gcs is not None')"
"M7 hub lets packets through during silence|tests/sim/twin/channel.py|s=s.replace('1.0 if seg[\"silent\"] else seg[\"loss\"], seg[\"delay_ms\"]','0.0 if seg[\"silent\"] else seg[\"loss\"], seg[\"delay_ms\"]')"
"M8 impedance ignored: usb_drop events are not applied|tests/sim/twin/schedule.py|s=s.replace('elif k == \"usb_drop\":','elif k == \"usb_dropx\":')"
"M9 seed ignored (draw 0 of seed 1 always)|tests/sim/twin/schedule.py|s=s.replace('th, rp = eng.draw(0, seed, False)\\n    ev = []','th, rp = eng.draw(0, 1, False)\\n    ev = []')"
"M10 delay not applied by the hub|tests/sim/twin/channel.py|s=s.replace('jitter_ms) if jitter_ms else delay_ms) / 1000.0','jitter_ms) if jitter_ms else delay_ms) * 0.0')"
"M11 invariant inverted (hub-silence contract)|tests/sim/twin/contracts.py|s=s.replace('% n) if n else (\"PASS\", \"nothing delivered','% n) if not n else (\"PASS\", \"nothing delivered')"
"M12 stall backlog not applied as delay|tests/sim/twin/schedule.py|s=s.replace('delay = base[\"delay_ms\"] + sum(float(i[3]) for i in act if i[2] == \"stall\")','delay = base[\"delay_ms\"]')"
"M13 speed not applied to real time|tests/sim/twin/schedule.py|s=s.replace('rr += (kb - ka) / sp','rr += (kb - ka)')"
"M14 jitter ignored by the hub|tests/sim/twin/channel.py|s=s.replace('rnd.gauss(delay_ms, jitter_ms) if jitter_ms else delay_ms','delay_ms')"
"M15 joystick-shared option ignored|tests/sim/twin/schedule.py|s=s.replace('cause + (\"+joystick\" if joy else \"\")','cause')"
"M16 FC model: radio failsafe never fires|tests/sim/apm_model.py|s=s.replace('and not self.overridden(t) and self.rc_override_time > 0','and not self.overridden(t) and self.rc_override_time > 1e9')"
"M17 silence cap not applied (outage not truncated)|tests/sim/twin/schedule.py|s=s.replace('g[\"played_len\"] = min(ln, cap)','g[\"played_len\"] = ln')"
"M18 hub loss decision inverted|tests/sim/twin/channel.py|s=s.replace('if rnd.random() < loss:','if rnd.random() > loss:')"
"M19 apm_fc.py ignores heartbeats|tests/sim/apm_fc.py|s=s.replace('fc.on_heartbeat(src, t)','pass')"
"M20 truncated time not reported (accounting)|tests/sim/twin/schedule.py|s=s.replace('trunc = sum(o[\"truncated_model_s\"] for o in outs)','trunc = 0.0')"
)

run_one() {
	local m="$1" id name r2 file expr c rc
	id="${m%% *}"
	local rest="${m#* }"
	name="${rest%%|*}"
	r2="${rest#*|}"
	file="${r2%%|*}"
	expr="${r2#*|}"
	c="$tmp/$id"
	mkdir -p "$c/tests/sim" "$c/bench"
	cp "$REPO"/tests/sim/*.py "$c/tests/sim/"
	cp -r "$REPO/tests/sim/models" "$c/tests/sim/models"
	cp -r "$HERE" "$c/tests/sim/twin"
	cp -r "$REPO/config" "$c/config"
	cp "$REPO/bench/tx12_bridge.py" "$c/bench/"
	find "$c" -name __pycache__ -type d -prune -exec rm -rf {} +
	if ! "$PY" - "$c/$file" "$expr" <<'PYEOF'
import sys
p, expr = sys.argv[1], sys.argv[2]
s = open(p).read()
o = s
exec(expr)
if s == o:
    sys.exit("mutation did not change the file: " + p)
open(p, "w").write(s)
PYEOF
	then
		echo "ERROR    $id: mutation not applied ($name)" >"$tmp/$id.res"
		return
	fi
	if ! "$PY" -m py_compile "$c/$file" 2>/dev/null; then
		echo "ERROR    $id: mutant is not valid Python ($name)" >"$tmp/$id.res"
		return
	fi
	timeout 90 "$c/tests/sim/twin/run.sh" --check >"$tmp/$id.log" 2>&1
	rc=$?
	if [ "$rc" = 0 ]; then
		echo "SURVIVED $id: $name" >"$tmp/$id.res"
	elif [ "$rc" = 124 ]; then
		echo "KILLED   $id: $name  <- (timeout: the mutant hangs the tests)" >"$tmp/$id.res"
	else
		echo "KILLED   $id: $name  <- $(grep -m1 -E '^(FAIL|ERROR):' "$tmp/$id.log" | cut -c1-110)" >"$tmp/$id.res"
	fi
	rm -rf "$c"
}

"$HERE/run.sh" --check >"$tmp/baseline.log" 2>&1 || { cat "$tmp/baseline.log"; echo "baseline (unmutated) run.sh --check fails: nothing to mutate"; exit 1; }
ids=()
for m in "${MUTS[@]}"; do
	id="${m%% *}"
	if [ ${#want[@]} -gt 0 ]; then
		hit=0
		for w in "${want[@]}"; do [ "$w" = "$id" ] && hit=1; done
		[ "$hit" = 1 ] || continue
	fi
	ids+=("$id")
	run_one "$m" &
	while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do sleep 0.2; done
done
wait

fail=0
killed=0
for id in "${ids[@]}"; do
	cat "$tmp/$id.res"
	case "$(cut -c1-3 "$tmp/$id.res")" in KIL) killed=$((killed + 1)) ;; *) fail=1 ;; esac
done
echo "mutation check: killed $killed of ${#ids[@]}"
[ "$fail" = 0 ] && echo "mutation check: all mutations killed" || echo "mutation check: SURVIVORS or errors"
exit "$fail"
