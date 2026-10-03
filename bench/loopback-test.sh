#!/usr/bin/env bash
# No-radio self test of the application layer on ONE machine:
#   video-src -> udp:5600 -> video-rx(fakesink)        (video chain, both codecs)
#   fake_fc <-> udp:14550 <-> gs_mav (RC + telemetry)   (MAVLink chain + timeouts)
# Tunables (durations, sizes, thresholds): LB_* keys in config/registry.tsv (docs/CONFIG.md).
# It does NOT test wfb-ng, drivers or RF. Exit code 0 = all checks passed.
. "$(dirname "$0")/lib.sh"

PY="${PY:-$VENV/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
"$PY" -c 'import pymavlink' 2>/dev/null || die "pymavlink missing for $PY (run setup-common.sh or set PY=)"

tmp="$(mktemp -d)"; pids=()
cleanup() { for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$tmp"; }
trap cleanup EXIT
fail=0
check() { if [ "$1" = 0 ]; then log "PASS  $2"; else warn "FAIL  $2"; fail=1; fi; }

# ---- MAVLink chain ----
"$PY" "$BENCH_DIR/fake_fc.py" --duration "$LB_FC_DURATION_S" --rc-override-time "$LB_FC_RC_OVERRIDE_TIME_S" --gcs-timeout "$LB_FC_GCS_TIMEOUT_S" >"$tmp/fc.log" 2>&1 & pids+=($!)
sleep 0.7
"$PY" "$BENCH_DIR/gs_mav.py" --rc sweep --confirm-props-off --duration "$LB_GS_DURATION_S" --selftest >"$tmp/gs.log" 2>&1
check $? "MAVLink telemetry + RC echo (gs_mav --selftest)"
sleep 4   # let fake_fc notice the missing RC overrides and GCS heartbeats
grep -q "RC override lost" "$tmp/fc.log"; check $? "RC override timeout detected"
grep -q "GCS failsafe" "$tmp/fc.log";    check $? "GCS heartbeat timeout detected"
grep "RTT ms" "$tmp/gs.log" | tail -1 | sed 's/.*RTT/RTT/' || true

# ---- video chain ----
if command -v gst-launch-1.0 >/dev/null; then
	for codec in h264 h265; do
		VIDEO_CODEC=$codec VIDEO_W="$LB_VIDEO_W" VIDEO_H="$LB_VIDEO_H" SINK_PORT="$GS_VIDEO_PORT" timeout 9 "$BENCH_DIR/video-src.sh" >"$tmp/src.$codec.log" 2>&1 & p=$!
		sleep 1.5
		VIDEO_CODEC=$codec SINK=fakesink PROGRESS=1 timeout 5 "$BENCH_DIR/video-rx.sh" >"$tmp/rx.$codec.log" 2>&1 || true
		kill "$p" 2>/dev/null || true; wait "$p" 2>/dev/null || true
		[ "$(grep -c progressreport "$tmp/rx.$codec.log")" -ge "$LB_VIDEO_MIN_PROGRESS" ]
		check $? "video $codec: frames flow src -> udp -> decode"
	done
else
	warn "gst-launch-1.0 not installed: video checks skipped"
fi

if [ "$fail" = 0 ]; then log "ALL CHECKS PASSED"; else warn "SOME CHECKS FAILED"; exit 1; fi
