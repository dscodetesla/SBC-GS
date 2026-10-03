#!/usr/bin/env bash
# No-hardware, no-root smoke test of every critical path. Prints PASS/FAIL/SKIP per check, exit 1 on any FAIL.
#   tests/sim/smoke.sh                # all paths in parallel, ~25 s
#   SMOKE_STRICT=1 tests/sim/smoke.sh # a SKIP (missing tool) counts as FAIL (use in CI)
#   SMOKE_ONLY="video mavlink" ...    # run a subset: static model mavlink video router wfb qemu
# Paths: static (syntax/lint/systemd/udev/board/gs-mavlink --print), model (failsafe model unit tests),
#        mavlink (fake_fc + gs_mav, tx12_bridge + apm_fc incl. dead-man and wrong-sysid), video (RTP over UDP,
#        latency), router (gs-mavlink.sh -> real mavp2p/mavlink-routerd if installed), wfb (wfb_veth.sh, needs root),
#        qemu (qemu_hwsim.sh: real wfb-ng over mac80211_hwsim in a QEMU guest, rootless, opt-in SMOKE_QEMU=1, ~30 s + 170 MB).
# Env: PY (python with pymavlink; default .venv or python3), GST_PY (python with gi; default /usr/bin/python3).
# It does NOT test radios, drivers, real FC/ArduPilot, Pi decoders or KMS output (see docs/TESTABILITY.md).
set -u
export PYTHONDONTWRITEBYTECODE=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BENCH="$ROOT/bench"
# shellcheck source=../../config/load.sh
. "$ROOT/config/load.sh"
sbc_cfg_load sim   # SMOKE_*, WFB_NG_REF, ... : layered configuration, docs/CONFIG.md
STRICT="$SMOKE_STRICT"
ONLY="$SMOKE_ONLY"

PY="${PY:-}"
if [ -z "$PY" ]; then
	for c in "$ROOT/.venv/bin/python" python3; do
		command -v "$c" >/dev/null 2>&1 && "$c" -c 'import pymavlink' 2>/dev/null && { PY="$c"; break; }
	done
fi
GST_PY="${GST_PY:-}"
if [ -z "$GST_PY" ]; then
	for c in /usr/bin/python3 /usr/bin/python3.12 /usr/bin/python3.13 /usr/bin/python3.11 python3; do
		command -v "$c" >/dev/null 2>&1 && "$c" -c 'import gi; gi.require_version("Gst","1.0")' 2>/dev/null && { GST_PY="$c"; break; }
	done
fi

tmp="$(mktemp -d)"
cleanup() { jobs -p | xargs -r kill 2>/dev/null; rm -rf "$tmp"; }
trap cleanup EXIT
: >"$tmp/results"
res() {  # PASS|FAIL|SKIP path "name"   (append is atomic for short lines)
	printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$tmp/results"
}
ok() { if [ "$1" = 0 ]; then res PASS "$2" "$3"; else res FAIL "$2" "$3"; fi; }
skip() { res SKIP "$1" "$2"; }
want() { case " $ONLY " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
# wait until a pattern appears in a file (max N s)
waitfor() { local f="$1" pat="$2" n="${3:-10}" i=0; while [ "$i" -lt $((n * 10)) ]; do grep -q -- "$pat" "$f" 2>/dev/null && return 0; sleep 0.1; i=$((i + 1)); done; return 1; }
# EVENT "<t>s <text>" -> t of the first event matching a pattern
evt() { sed -n "s/^EVENT \([0-9.]*\)s .*$2.*/\1/p" "$1" | head -1; }

# ---------------------------------------------------------------- static
p_static() {
	local d="$tmp/static" f rc bad=0
	mkdir -p "$d"
	for f in "$ROOT"/gs/*.sh "$ROOT"/gs/lib/*.sh "$ROOT"/gs/mavlink/*.sh "$ROOT"/gs/boards/*.sh "$ROOT"/bench/*.sh \
		"$ROOT"/tests/*.sh "$ROOT"/tests/sim/*.sh "$ROOT"/build/*.sh "$ROOT"/build/lib/*.sh; do
		[ -f "$f" ] || continue
		bash -n "$f" 2>>"$d/bashn.log" || { echo "bash -n failed: $f" >>"$d/bashn.log"; bad=1; }
	done
	ok "$bad" static "bash -n on all shell scripts"
	for f in "$ROOT"/bench/*.py "$ROOT"/gs/*.py "$ROOT"/tests/sim/*.py; do
		"${PY:-python3}" -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$f" 2>>"$d/py.log" || bad=2
	done
	ok "$([ "$bad" = 2 ] && echo 1 || echo 0)" static "python syntax (ast.parse) of bench/ gs/ tests/sim/"
	if command -v shellcheck >/dev/null; then
		( cd "$ROOT/tests/sim" && shellcheck -x -S warning ./*.sh ) >"$d/sc.log" 2>&1; ok $? static "shellcheck -x -S warning tests/sim/*.sh"
	else skip static "shellcheck not installed"; fi
	if command -v systemd-analyze >/dev/null; then
		mkdir -p "$d/units"
		for f in "$ROOT"/gs/gs.service "$ROOT"/gs/gs-init.service "$ROOT"/gs/mavlink/gs-mavlink.service; do
			# units point at /gs/...: rewrite to the checkout so verify can see the executables (no root, no /gs needed)
			sed "s#/gs/#$ROOT/gs/#g" "$f" >"$d/units/$(basename "$f")"
		done
		systemd-analyze verify "$d"/units/*.service >"$d/sd.log" 2>&1; rc=$?
		# verify prints unknown keys/values as warnings and still exits 0: any output is a failure
		[ "$rc" = 0 ] && [ ! -s "$d/sd.log" ]; ok $? static "systemd-analyze verify gs.service gs-init.service gs-mavlink.service"
	else skip static "systemd-analyze not installed"; fi
	if command -v udevadm >/dev/null && udevadm verify --help >/dev/null 2>&1; then
		udevadm verify "$ROOT"/gs/98-rename.rules "$ROOT"/gs/99-GS.rules >"$d/udev.log" 2>&1; ok $? static "udevadm verify gs/*.rules"
	else skip static "udevadm verify not available (needs systemd >= 254)"; fi
	"$ROOT/gs/boards/validate.sh" >"$d/board.log" 2>&1; ok $? static "board profiles validate (radxa-zero3, rpi4)"
	bad=0
	for f in "$ROOT"/tests/fixtures/gs-mavlink/*.conf; do
		out="$(GS_MAVLINK_CONF="$f" "$ROOT/gs/mavlink/gs-mavlink.sh" --print 2>/dev/null)"; rc=$?
		case "$(basename "$f")" in
			bad-*|dup-*) [ "$rc" = 2 ] || { echo "$f: want rc 2 got $rc" >>"$d/gm.log"; bad=1; } ;;
			*) [ "$rc" = 0 ] && printf '%s\n' "$out" | grep -qE '^(mavp2p|mavlink-routerd) ' || { echo "$f: rc=$rc" >>"$d/gm.log"; bad=1; } ;;
		esac
	done
	ok "$bad" static "gs-mavlink.sh --print: 5 valid fixtures give a command, 4 invalid exit 2"
}

# ---------------------------------------------------------------- model
p_model() {
	[ -n "$PY" ] || { skip model "no python"; return; }
	"$PY" "$HERE/test_apm_model.py" >"$tmp/model.log" 2>&1; ok $? model "ArduPilot failsafe model (apm_model) unit tests: $(grep -c '^PASS' "$tmp/model.log") checks"
}

# ---------------------------------------------------------------- mavlink
p_mavlink() {
	[ -n "$PY" ] || { skip mavlink "pymavlink missing (pip install -r bench/requirements.txt)"; return; }
	local d="$tmp/mav"; mkdir -p "$d"
	# A: telemetry + RC echo + documented timeouts (fake_fc <-> gs_mav)
	(
		"$PY" "$BENCH/fake_fc.py" --conn udpout:127.0.0.1:$SMOKE_PORT_FC --duration 11 --rc-override-time 1 --gcs-timeout 2 >"$d/fc.log" 2>&1 &
		sleep 0.7
		"$PY" "$BENCH/gs_mav.py" --conn udpin:127.0.0.1:$SMOKE_PORT_FC --rc sweep --confirm-props-off --duration 4 --selftest >"$d/gs.log" 2>&1
		ok $? mavlink "telemetry + RC echo (gs_mav --selftest vs fake_fc)"
		sleep 3.5
		grep -q "RC override lost" "$d/fc.log"; ok $? mavlink "fake_fc: RC override timeout detected"
		grep -q "GCS failsafe" "$d/fc.log"; ok $? mavlink "fake_fc: GCS heartbeat timeout detected"
	) &
	# B: real tx12_bridge (sweep) vs ArduPilot-semantics FC: start, expiry after exit, failsafes
	(
		"$PY" "$HERE/apm_fc.py" --conn udpin:127.0.0.1:$SMOKE_PORT_APM_A --duration 9 --rc-override-time 1 --rc-fs-timeout 1 \
			--fs-gcs-enable 1 --fs-gcs-timeout 2 >"$d/apmA.log" 2>&1 &
		sleep 0.7
		"$PY" "$BENCH/tx12_bridge.py" --input sweep --conn udpout:127.0.0.1:$SMOKE_PORT_APM_A --confirm-props-off --duration 3 \
			--lock "$d/lockA" >"$d/brA.log" 2>&1
		ok $? mavlink "tx12_bridge sweep exits 0 (releases channels on exit)"
		sleep 6.2
		[ -n "$(evt "$d/apmA.log" 'RC override started')" ] && [ -n "$(evt "$d/apmA.log" 'RC override expired')" ]
		ok $? mavlink "RC bridge -> apm_fc: override started, then expired after the bridge exited"
		[ -n "$(evt "$d/apmA.log" 'GCS Failsafe')" ] && [ -n "$(evt "$d/apmA.log" 'Radio Failsafe')" ]
		ok $? mavlink "bridge gone: GCS failsafe and radio failsafe both trip (FS_GCS_ENABLE=1, no receiver)"
	) &
	# C: dead-man: stdin input stops, the bridge must stop sending sticks and release (override expires)
	(
		"$PY" "$HERE/apm_fc.py" --conn udpin:127.0.0.1:$SMOKE_PORT_APM_B --duration 7 --rc-override-time 3 >"$d/apmB.log" 2>&1 &
		sleep 0.7
		{ for _ in $(seq 30); do echo "1600 1500 1300 1500 1500 1500 1500 1500"; sleep 0.05; done; sleep 4; } |
			"$PY" "$BENCH/tx12_bridge.py" --input stdin --conn udpout:127.0.0.1:$SMOKE_PORT_APM_B --confirm-props-off --deadman-ms "$SMOKE_DEADMAN_MS" \
				--duration 6 --lock "$d/lockB" >"$d/brB.log" 2>&1
		sleep 1.5
		s="$(evt "$d/apmB.log" 'RC override started')"; e="$(evt "$d/apmB.log" 'RC override expired')"
		# input lasts 1.5 s; dead-man 0.3 s; release follows. RC_OVERRIDE_TIME is 3 s, so an expiry within
		# 3 s of the start proves the BRIDGE released the channels instead of waiting for the FC timeout.
		[ -n "$s" ] && [ -n "$e" ] && awk -v s="$s" -v e="$e" -v m="$SMOKE_DEADMAN_RELEASE_MAX_S" 'BEGIN{d=e-s; exit !(d>=0.2 && d<=m)}'
		ok $? mavlink "dead-man: bridge releases channels ${s:+$(awk -v s="$s" -v e="${e:-0}" 'BEGIN{printf "%.1f s after start (<3 s FC timeout)", e-s}')}"
	) &
	# D: a non-GCS sysid must not be able to drive the FC (single RC writer rule)
	(
		"$PY" "$HERE/apm_fc.py" --conn udpin:127.0.0.1:$SMOKE_PORT_APM_C --duration 4.5 >"$d/apmC.log" 2>&1 &
		sleep 0.7
		"$PY" "$BENCH/tx12_bridge.py" --input sweep --sysid 77 --conn udpout:127.0.0.1:$SMOKE_PORT_APM_C --confirm-props-off --duration 2 \
			--lock "$d/lockC" >"$d/brC.log" 2>&1
		sleep 1.2
		grep -q "sysid 77 ignored" "$d/apmC.log" && [ -z "$(evt "$d/apmC.log" 'RC override started')" ]
		ok $? mavlink "overrides from non-GCS sysid 77 are ignored by the FC model"
	) &
	wait
}

# ---------------------------------------------------------------- video
p_video() {
	command -v gst-launch-1.0 >/dev/null || { skip video "gst-launch-1.0 missing (gstreamer1.0-tools/-plugins-*)"; return; }
	local d="$tmp/vid"; mkdir -p "$d"
	gst-inspect-1.0 x264enc >/dev/null 2>&1 && gst-inspect-1.0 avdec_h264 >/dev/null 2>&1 || { skip video "x264enc/avdec_h264 missing (plugins-ugly, libav)"; return; }
	(
		VIDEO_CODEC=h264 VIDEO_W=640 VIDEO_H=360 SINK_PORT=$SMOKE_PORT_VIDEO timeout 10 "$BENCH/video-src.sh" >"$d/src.log" 2>&1 &
		sleep 1.2
		VIDEO_CODEC=h264 LISTEN_PORT=$SMOKE_PORT_VIDEO SINK=fakesink PROGRESS=1 timeout 6 "$BENCH/video-rx.sh" >"$d/rx.log" 2>&1
		n="$(grep -c progressreport "$d/rx.log")"
		[ "$n" -ge "$SMOKE_VIDEO_MIN_PROGRESS" ]; ok $? video "bench/video-src.sh -> udp -> bench/video-rx.sh (h264, fakesink): $n progress reports"
	) &
	(
		if [ -z "$GST_PY" ]; then skip video "latency: python3-gi + gir1.2-gstreamer-1.0 missing"; exit 0; fi
		for c in h264 h265; do
			gst-inspect-1.0 "x${c#h}enc" >/dev/null 2>&1 || { skip video "latency $c: encoder missing"; continue; }
			port=$((15610 + ${#c}))
			[ "$c" = h265 ] && port=15620
			line="$("$GST_PY" "$HERE/video_latency.py" --codec "$c" --port "$port" 2>"$d/lat.$c.err" | tail -1)"; rc=$?
			ok "$rc" video "software latency $c (no drops, p95 <= 500 ms): ${line#LATENCY }"
		done
	) &
	wait
}

# ---------------------------------------------------------------- router
p_router() {
	[ -n "$PY" ] || { skip router "pymavlink missing"; return; }
	local r d="$tmp/rt"; mkdir -p "$d"
	r="$(command -v mavp2p || true)"
	[ -n "$r" ] || { skip router "mavp2p not installed (go install github.com/bluenviron/mavp2p@v1.3.3 or mavlink-routerd)"; return; }
	printf "ROUTER='mavp2p'\nUPSTREAM_PORT='%s'\nGCS_UDP_PORTS='%s'\nHB_SYSID='125'\n" "$SMOKE_PORT_ROUTER_UP" "$SMOKE_PORT_ROUTER_GCS" >"$d/gs-mavlink.conf"
	GS_MAVLINK_CONF="$d/gs-mavlink.conf" "$ROOT/gs/mavlink/gs-mavlink.sh" >"$d/router.log" 2>&1 & local rp=$!
	sleep 1
	"$PY" "$BENCH/fake_fc.py" --conn udpout:127.0.0.1:$SMOKE_PORT_ROUTER_UP --duration 8 >"$d/fc.log" 2>&1 & local fp=$!
	sleep 1
	"$PY" "$BENCH/gs_mav.py" --conn udpout:127.0.0.1:$SMOKE_PORT_ROUTER_GCS --duration 4 --selftest >"$d/gs.log" 2>&1
	ok $? router "gs-mavlink.sh starts real mavp2p; telemetry fake_fc -> router -> gs_mav"
	kill "$rp" "$fp" 2>/dev/null; pkill -P "$rp" 2>/dev/null
	wait "$rp" "$fp" 2>/dev/null
}

# ---------------------------------------------------------------- wfb
p_wfb() {
	if [ "$(id -u)" != 0 ] && ! sudo -n true 2>/dev/null; then skip wfb "needs root/sudo (veth + AF_PACKET); run: sudo tests/sim/wfb_veth.sh"; return; fi
	[ -n "${WFB_NG_DIR:-}" ] || [ "${SMOKE_WFB:-0}" = 1 ] || { skip wfb "set WFB_NG_DIR=<built wfb-ng tree> or SMOKE_WFB=1 (fetches+builds wfb-ng, needs network and libsodium/libpcap/libevent dev packages)"; return; }
	PY="${PY:-python3}" "$HERE/wfb_veth.sh" >"$tmp/wfb.log" 2>&1; rc=$?
	if [ "$rc" = 77 ]; then skip wfb "$(tail -1 "$tmp/wfb.log")"; else ok "$rc" wfb "real wfb_tx/wfb_rx over veth + virtual air (loss recovered by FEC)"; fi
}

# ---------------------------------------------------------------- qemu
p_qemu() {
	[ "${SMOKE_QEMU:-0}" = 1 ] || [ "$ONLY" = qemu ] || { skip qemu "opt-in: SMOKE_QEMU=1 (downloads ~170 MB kernel packages, ~30 s under TCG)"; return; }
	"$HERE/qemu_hwsim.sh" >"$tmp/qemu.log" 2>&1; rc=$?
	if [ "$rc" = 77 ]; then skip qemu "$(tail -1 "$tmp/qemu.log")"; else ok "$rc" qemu "real wfb-ng over mac80211_hwsim in QEMU (monitor, injection, FEC link, 2 negative controls)"; fi
}

for p in $ONLY; do
	want "$p" && "p_$p" &
done
wait

order="static model mavlink video router wfb qemu"
npass=0; nfail=0; nskip=0
for p in $order; do
	while IFS=$'\t' read -r st path name; do
		[ "$path" = "$p" ] || continue
		printf '%-4s  %-8s %s\n' "$st" "$path" "$name"
		case "$st" in PASS) npass=$((npass + 1)) ;; FAIL) nfail=$((nfail + 1)) ;; SKIP) nskip=$((nskip + 1)); [ "$STRICT" = 1 ] && nfail=$((nfail + 1)) ;; esac
	done < <(sort -s -k2,2 "$tmp/results" | awk -F'\t' -v p="$p" '$2==p')
done
echo "smoke: $npass passed, $nfail failed, $nskip skipped (SMOKE_STRICT=$STRICT)"
if [ "$nfail" -gt 0 ] && [ -n "${SMOKE_KEEP_LOGS:-}" ]; then cp -r "$tmp" "$SMOKE_KEEP_LOGS"; fi
[ "$nfail" = 0 ]
