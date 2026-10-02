#!/usr/bin/env bash
# No-hardware test of bench/tx12_bridge.py (safe single-writer RC bridge) over UDP
# loopback. A tiny FC stand-in (sniffer) logs every RC_CHANNELS_OVERRIDE with a
# timestamp; one scenario also runs against the real bench/fake_fc.py.
# Asserts: refusal without safety flag, clamping, NaN/absurd rejection, dead-man
# timing, release-then-silence, lock (second instance refused), exit releases,
# real-fc channel masking, rate guard, lazy evdev import. Exit 0 = all passed.
# It does NOT test a TX12, evdev devices or a real FC (needs hardware).
. "$(dirname "$0")/lib.sh"
set +e   # failures are collected by check(), not fatal

PY="${PY:-$VENV/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
"$PY" -c 'import pymavlink' 2>/dev/null || die "pymavlink missing for $PY (run setup-common.sh or set PY=)"

tmp="$(mktemp -d)"; pids=()
cleanup() { for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$tmp"; }
trap cleanup EXIT
fail=0
check() { if [ "$1" = 0 ]; then log "PASS  $2"; else warn "FAIL  $2"; fail=1; fi; }

BR="$BENCH_DIR/tx12_bridge.py"
PORT=$((15000 + $$ % 2000))
nextport() { PORT=$((PORT + 1)); }

# FC stand-in: HEARTBEAT 2 Hz once a peer is known, logs "ts ch1..ch8" per override.
cat >"$tmp/sniff.py" <<'EOF'
import sys, time
from pymavlink import mavutil
port, dur, out = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
m = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}", source_system=1, source_component=1)
t0 = last = 0.0
t0 = time.time()
peer = False
with open(out, "w") as f:
    while time.time() - t0 < dur:
        msg = m.recv_match(blocking=True, timeout=0.02)
        now = time.time()
        if msg is not None:
            peer = True
            if msg.get_type() == "RC_CHANNELS_OVERRIDE":
                f.write("%.4f %d %d %d %d %d %d %d %d %d\n" % (
                    now, msg.target_system, msg.chan1_raw, msg.chan2_raw, msg.chan3_raw, msg.chan4_raw,
                    msg.chan5_raw, msg.chan6_raw, msg.chan7_raw, msg.chan8_raw))
                f.flush()
        if peer and now - last >= 0.5:
            last = now
            m.mav.heartbeat_send(2, 3, 0, 0, 4)
    f.write("END %.4f\n" % time.time())
EOF

# Analyser: python snippets read the sniff log (frames = list of (ts, [8 values])).
cat >"$tmp/an.py" <<'EOF'
import sys
def load(p):
    fr, end = [], None
    for l in open(p):
        t = l.split()
        if t[0] == "END":
            end = float(t[1])
        else:
            fr.append((float(t[0]), int(t[1]), [int(x) for x in t[2:]]))
    return fr, end
EOF

start_sniff() { "$PY" "$tmp/sniff.py" "$PORT" "$1" "$2" & pids+=($!); sleep 0.6; }
CONNARG() { echo "udpout:127.0.0.1:$PORT"; }

# ---- 1. refusal without a safety flag ----
"$PY" "$BR" --input sweep --lock "$tmp/l0" --conn "$(CONNARG)" >"$tmp/r1.log" 2>&1; rc=$?
[ "$rc" != 0 ] && grep -q -- "--confirm-props-off" "$tmp/r1.log"
check $? "refuses to start without --confirm-props-off/--real-fc-armed-ok (rc=$rc)"
"$PY" "$BR" --input sweep --confirm-props-off --rate 30 --max-rate 20 --lock "$tmp/l0" >"$tmp/r1b.log" 2>&1
[ $? != 0 ]; check $? "refuses --rate above --max-rate"

# ---- 2. lazy evdev import / clear error ----
PYTHONPATH="$BENCH_DIR" "$PY" -c 'import sys, tx12_bridge; assert "evdev" not in sys.modules' 2>"$tmp/r2.log"
check $? "python-evdev is not imported unless --input evdev is selected"
"$PY" "$BR" --input evdev:/dev/null --confirm-props-off --lock "$tmp/l0" >"$tmp/r2b.log" 2>&1
[ $? != 0 ]; check $? "--input evdev:/dev/null fails cleanly (no evdev or not a device)"

# ---- 3. axis mapping unit checks (calibration, deadband, reverse, clamp) ----
export BENCH_DIR
PYTHONPATH="$BENCH_DIR" "$PY" - <<'EOF'
import tx12_bridge as b
bi = {"min": -1024, "max": 1024, "center": 0, "deadband": 0.05}
assert b.map_axis(0, bi) == 1500 and b.map_axis(40, bi) == 1500          # inside deadband
assert b.map_axis(1024, bi) == 2000 and b.map_axis(-1024, bi) == 1000
assert b.map_axis(99999, bi) == 2000 and b.map_axis(-99999, bi) == 1000    # clamped
assert b.map_axis(1024, dict(bi, reverse=True)) == 1000
lin = {"min": 0, "max": 1000}
assert b.map_axis(0, lin) == 1000 and b.map_axis(500, lin) == 1500 and b.map_axis(1000, lin) == 2000
assert b.map_axis(1000, dict(lin, reverse=True)) == 1000
assert b.sanitize([float("nan")]) is None and b.sanitize([1500, 1e9]) is None and b.sanitize([]) is None
assert b.sanitize([2500, 500]) == [2000, 1000] + [65535] * 6
assert len(b.load_map("%s/tx12_map.example.json" % __import__("os").environ["BENCH_DIR"])) == 4
EOF
check $? "axis mapping / sanitize unit checks"

# ---- 4. stdin: clamping, NaN/absurd dropped, dead-man timing ----
nextport; start_sniff 9 "$tmp/s4.log"
{
	sleep 1.2
	for _ in $(seq 10); do echo "2500 500 1500 1500 1500 1500 1500 1500"; echo "nan 1500 1500"; echo "99999 1500"; sleep 0.05; done
	for _ in $(seq 6); do echo "1100 1900"; sleep 0.05; done
	for _ in $(seq 6); do echo "1200 1800 1500 1500 1500 1500 1500 1500"; sleep 0.05; done
	date +%s.%N >"$tmp/t_last"
	sleep 4
} | "$PY" "$BR" --input stdin --confirm-props-off --conn "$(CONNARG)" --lock "$tmp/l4" --deadman-ms 300 --duration 7 >"$tmp/b4.log" 2>&1
wait "${pids[-1]}" 2>/dev/null || true
"$PY" - "$tmp" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from an import load
tmp = sys.argv[1]
fr, end = load(tmp + "/s4.log")
t_last = float(open(tmp + "/t_last").read())
ok = lambda v: v == 0 or v == 65535 or 1000 <= v <= 2000
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name);
    if not cond: sys.exit(1)
check("frames received", len(fr) > 20)
check("all values in {0,65535,1000..2000}", all(ok(v) for _, _, vs in fr for v in vs))
check("target sysid is the FC (1)", all(s == 1 for _, s, _ in fr))
check("2500/500 arrive clamped to 2000/1000", any(vs[0] == 2000 and vs[1] == 1000 for _, _, vs in fr))
check("short line: unmapped channels = 65535", any(vs[0] == 1100 and vs[1] == 1900 and vs[2:] == [65535] * 6 for _, _, vs in fr))
after = [(t, vs) for t, _, vs in fr if t > t_last + 0.02]
fs = [t for t, vs in after if vs == [0, 0, 1000, 0, 0, 0, 0, 0]]
zero = [t for t, vs in after if vs == [0] * 8]
check("throttle failsafe frame sent after dead-man", len(fs) >= 1)
d = fs[0] - t_last
print("dead-man latency from last input to first failsafe frame: %.3f s" % d)
check("dead-man fires within 0.20..0.60 s (deadman 0.3 s)", 0.20 <= d <= 0.60)
check("no stick frames after the failsafe frame", all(vs == [0, 0, 1000, 0, 0, 0, 0, 0] or vs == [0] * 8 for t, vs in after if t >= fs[0]))
check("release (all 0) frames follow", len(zero) >= 10)
span = zero[-1] - fs[0]
print("failsafe+release span: %.3f s, then silent until log end %.1f s later" % (span, end - zero[-1]))
check("release lasts ~1 s (0.8..1.3)", 0.8 <= span <= 1.3)
check("then silence (>=1.5 s without frames)", end - zero[-1] >= 1.5)
EOF
check $? "stdin: clamp, NaN/absurd dropped, dead-man -> failsafe -> release -> silence"

# ---- 5. single writer lock ----
nextport
"$PY" "$BR" --input stdin --confirm-props-off --conn "$(CONNARG)" --lock "$tmp/l5" --duration 4 </dev/null >"$tmp/b5.log" 2>&1 & p1=$!; pids+=("$p1")
sleep 1
"$PY" "$BR" --input sweep --confirm-props-off --conn "$(CONNARG)" --lock "$tmp/l5" --duration 2 >"$tmp/b5b.log" 2>&1; rc=$?
[ "$rc" = 3 ] && grep -q "REFUSED" "$tmp/b5b.log"
check $? "second instance refused by lock (rc=$rc)"
wait "$p1" 2>/dev/null
"$PY" "$BR" --input stdin --confirm-props-off --conn "$(CONNARG)" --lock "$tmp/l5" --duration 0.5 </dev/null >"$tmp/b5c.log" 2>&1
check $? "lock is free again after the first instance exited"

# ---- 6. exit releases (SIGTERM and SIGINT), sweep, rate and gap guard ----
for sig in TERM INT; do
	nextport; start_sniff 8 "$tmp/s6$sig.log"
	"$PY" "$BR" --input sweep --confirm-props-off --conn "$(CONNARG)" --lock "$tmp/l6" --max-rate 20 --duration 30 >"$tmp/b6$sig.log" 2>&1 & bp=$!; pids+=("$bp")
	sleep 3.5
	kill -"$sig" "$bp"; t_kill="$(date +%s.%N)"
	wait "$bp"; rc=$?
	t_end="$(date +%s.%N)"
	wait "${pids[-2]}" 2>/dev/null || true
	"$PY" - "$tmp" "s6$sig.log" "$t_kill" "$t_end" "$rc" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from an import load
fr, end = load(sys.argv[1] + "/" + sys.argv[2])
t_kill, t_end, rc = float(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5])
tail = [vs for _, _, vs in fr[-8:]]
sticks = [t for t, _, vs in fr if vs[0] not in (0,) and vs[0] != 65535]
gaps = [b[0] - a[0] for a, b in zip(fr, fr[1:])]
rate = (len(sticks) - 1) / (sticks[-1] - sticks[0])
print("exit rc=%d, exit took %.2f s, tail=%s, stick rate=%.1f Hz, min gap=%.3f s" % (rc, t_end - t_kill, tail[-1], rate, min(gaps)))
assert rc == 0 and t_end - t_kill < 2.0
assert tail[:3] == [[0, 0, 1000, 0, 0, 0, 0, 0]] * 3 and tail[3:] == [[0] * 8] * 5   # fs x3 then release x5
assert 15 <= rate <= 25
assert min(gaps) >= 0.035   # --max-rate 20 guard (50 ms) minus socket jitter
EOF
	check $? "SIG$sig: throttle failsafe x3 + release x5, clean exit, ~20 Hz, --max-rate gap held"
done

# ---- 7. --real-fc-armed-ok masks channels 5-8 ----
nextport; start_sniff 4 "$tmp/s7.log"
"$PY" "$BR" --input sweep --real-fc-armed-ok --conn "$(CONNARG)" --lock "$tmp/l7" --duration 2 >"$tmp/b7.log" 2>&1
wait "${pids[-1]}" 2>/dev/null || true
"$PY" - "$tmp" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from an import load
fr, _ = load(sys.argv[1] + "/s7.log")
st = [vs for _, _, vs in fr if vs[0] != 0]
assert len(st) > 10 and all(vs[4:] == [65535] * 4 and all(1000 <= v <= 2000 for v in vs[:4]) for vs in st)
EOF
check $? "--real-fc-armed-ok: channels 5-8 = 65535, 1-4 clamped"

# ---- 8. against the real bench/fake_fc.py ----
nextport
"$PY" "$BENCH_DIR/fake_fc.py" --conn "udpin:127.0.0.1:$PORT" --rc-override-time 1 --gcs-timeout 30 --duration 8 >"$tmp/fc.log" 2>&1 & pids+=($!)
sleep 0.7
"$PY" "$BR" --input sweep --confirm-props-off --conn "$(CONNARG)" --lock "$tmp/l8" --duration 3 >"$tmp/b8.log" 2>&1
check $? "bridge sweep against fake_fc.py exits 0"
grep -q "FC sysid=1" "$tmp/b8.log"; check $? "bridge learned the FC sysid from its HEARTBEAT"
sleep 2.2
grep -q "RC override started" "$tmp/fc.log"; check $? "fake_fc saw RC override start"
grep -q "RC override lost" "$tmp/fc.log"; check $? "fake_fc saw override stop after bridge exit (released, then silent)"

if [ "$fail" = 0 ]; then log "ALL TX12 BRIDGE CHECKS PASSED"; else warn "SOME TX12 BRIDGE CHECKS FAILED"; exit 1; fi
