#!/usr/bin/env bash
# Real wfb-ng data plane (wfb_tx -> FEC + crypto -> 802.11 frames -> wfb_rx) over veth pairs, no radio.
#   wfb_tx -> [wtx0]==[air0] -> air_relay.py (loss/delay) -> [air1]==[wrx0] -> wfb_rx (LD_PRELOAD pcapshim.so)
# Needs: root (or passwordless sudo), iproute2, python3, gcc, libsodium-dev libpcap-dev libevent-dev.
# wfb-ng comes from WFB_NG_DIR (a tree where `make all_bin` was run) or is fetched at the pinned commit.
# Exit: 0 pass, 1 fail, 77 skipped (prerequisite missing; nothing was tested).
# Proves: FEC recovers loss, session/crypto handshake works, ports and keys wired as in gs/ and bench/.
# Does NOT prove: RF, driver, monitor-mode injection, real radiotap/MCS/rssi, USB, antenna diversity timing.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WFB_NG_REF="${WFB_NG_REF:-2fe252b2f451c1ccfb16968e064fe1cdb18baaa0}"
PY="${PY:-python3}"
skip() { echo "SKIP wfb_veth: $*"; exit 77; }

SUDO=""
if [ "$(id -u)" != 0 ]; then
	sudo -n true 2>/dev/null || skip "needs root or passwordless sudo (veth + AF_PACKET)"
	SUDO="sudo -n"
fi
for t in ip gcc make git; do command -v "$t" >/dev/null || skip "$t not installed"; done
$SUDO ip link add simchk0 type veth peer name simchk1 2>/dev/null || skip "cannot create veth (no CAP_NET_ADMIN or no veth in kernel)"
$SUDO ip link del simchk0

tmp="$(mktemp -d)"; pids=()
cleanup() {
	for p in "${pids[@]:-}"; do $SUDO kill "$p" 2>/dev/null || true; done
	for l in wtx0 air1; do $SUDO ip link del "$l" 2>/dev/null || true; done
	[ -n "${KEEP_TMP:-}" ] || rm -rf "$tmp"
}
trap cleanup EXIT

src="${WFB_NG_DIR:-}"
if [ -z "$src" ]; then
	src="$tmp/wfb-ng"; mkdir "$src"
	( cd "$src" && git init -q && git fetch -q --depth 1 https://github.com/svpcom/wfb-ng.git "$WFB_NG_REF" && git checkout -q FETCH_HEAD ) \
		|| skip "cannot fetch wfb-ng $WFB_NG_REF (network?)"
fi
[ -x "$src/wfb_tx" ] && [ -x "$src/wfb_rx" ] || ( cd "$src" && make all_bin >"$tmp/build.log" 2>&1 ) \
	|| skip "wfb-ng build failed (see build.log; need libsodium-dev libpcap-dev libevent-dev g++): $(tail -2 "$tmp/build.log" 2>/dev/null)"
gcc -shared -fPIC -o "$tmp/pcapshim.so" "$HERE/pcapshim.c" || skip "cannot build pcapshim.so"

( cd "$tmp" && "$src/wfb_keygen" >/dev/null 2>&1 ) || skip "wfb_keygen failed"
for pair in "wtx0 air0" "air1 wrx0"; do
	set -- $pair
	$SUDO ip link add "$1" type veth peer name "$2" && $SUDO ip link set "$1" up && $SUDO ip link set "$2" up || skip "veth setup failed"
done

# start the two wfb-ng ends (video-style stream, radio port 0, default FEC 8/12)
$SUDO env LD_PRELOAD="$tmp/pcapshim.so" "$src/wfb_rx" -p 0 -c 127.0.0.1 -u 15700 -K "$tmp/gs.key" wrx0 >"$tmp/rx.log" 2>&1 & pids+=($!)
$SUDO "$src/wfb_tx" -p 0 -u 15702 -K "$tmp/drone.key" wtx0 >"$tmp/tx.log" 2>&1 & pids+=($!)
sleep 0.5

fail=0
scenario() {  # name, relay args..., then expected-condition awk on PROBE line (name min_delivery)
	local name="$1" mind="$2"; shift 2
	$SUDO "$PY" "$HERE/air_relay.py" --src air0 --dst air1 "$@" >"$tmp/relay.log" 2>&1 & local rp=$!
	sleep 0.5
	local out rc=0
	out="$("$PY" "$HERE/udp_probe.py" --send-to 127.0.0.1:15702 --listen 127.0.0.1:15700 --count 200 --rate 100 --min-delivery "$mind")" || rc=$?
	$SUDO kill "$rp" 2>/dev/null; wait "$rp" 2>/dev/null
	echo "  $out"
	echo "  $(cat "$tmp/relay.log")"
	if [ "$rc" = 0 ]; then echo "PASS  wfb-ng over veth: $name"; else echo "FAIL  wfb-ng over veth: $name"; fail=1; fi
}
scenario "clean air, >=99% delivered" 0.99
scenario "10% frame loss, FEC 8/12 recovers (>=90%)" 0.90 --loss 0.10 --seed 7
# negative control: 60% loss must NOT pass a 90% threshold, otherwise the relay does not impair anything
$SUDO "$PY" "$HERE/air_relay.py" --src air0 --dst air1 --loss 0.6 --seed 3 >"$tmp/relay.log" 2>&1 & rp=$!; sleep 0.5
if "$PY" "$HERE/udp_probe.py" --send-to 127.0.0.1:15702 --listen 127.0.0.1:15700 --count 200 --rate 100 --min-delivery 0.90 >"$tmp/neg.out"; then
	echo "FAIL  negative control: 60% loss still delivered >=90% ($(cat "$tmp/neg.out"))"; fail=1
else echo "PASS  negative control: 60% loss is visible ($(cat "$tmp/neg.out"))"; fi
$SUDO kill "$rp" 2>/dev/null; wait "$rp" 2>/dev/null
[ "$fail" = 0 ] && echo "wfb_veth: ALL PASS" || { echo "wfb_veth: FAILED"; exit 1; }
