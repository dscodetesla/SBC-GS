#!/usr/bin/env bash
# Preflight / status for a node. No changes are made. Usage: ./check.sh <air|gs>
. "$(dirname "$0")/lib.sh"
role_check "${1:-}"
rc=0
ok()   { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; rc=1; }
note() { printf '  \033[1;33mINFO\033[0m %s\n' "$*"; }
t()    { if eval "$1" >/dev/null 2>&1; then ok "$2"; else bad "$2 ${3:+-> $3}"; fi; }

echo "== $1 node: $(hostname), kernel $(uname -r), $(uname -m)"
t 'command -v wfb_rx' "wfb-ng binaries installed" "sudo ./install-wfb.sh $1"
if [ "$1" = gs ]; then
	t '[ -f /etc/gs.key ]' "/etc/gs.key present" "sudo ./install-wfb.sh gs"
	svc=wifibroadcast@gs
else
	t '[ -f /etc/drone.key ]' "/etc/drone.key present" "scp from the GS"
	svc=wifibroadcast@drone
fi
t '[ -f /etc/wifibroadcast.cfg ]' "/etc/wifibroadcast.cfg present" "sudo ./configure-wfb.sh $1"
t 'lsmod | grep -qE "88XXau_wfb|8812eu"' "patched Realtek module loaded" "sudo ./install-driver.sh && reboot"
t 'systemctl is-active --quiet '"$svc" "service $svc active" "journalctl -u $svc -n 30"
t "\"$VENV/bin/python\" -c 'import pymavlink'" "pymavlink in $VENV" "sudo ./setup-common.sh $1"

for n in $(iw dev 2>/dev/null | awk '/Interface/{print $2}'); do
	note "iface $n: $(iw dev "$n" info 2>/dev/null | awk '/type|channel/{printf "%s ", $0}')"
done
if command -v ethtool >/dev/null; then
	for n in $(iw dev 2>/dev/null | awk '/Interface/{print $2}'); do
		note "iface $n driver: $(ethtool -i "$n" 2>/dev/null | awk '/^driver|^version/{printf "%s ", $0}')"
	done
fi
if command -v vcgencmd >/dev/null; then
	note "SoC $(vcgencmd measure_temp 2>/dev/null)  throttled=$(vcgencmd get_throttled 2>/dev/null)  (0x0 = clean; non-zero = power/thermal problem)"
fi
if [ "$1" = air ]; then
	t 'systemctl is-active --quiet bench-video-src' "bench-video-src running" "sudo systemctl start bench-video-src"
	if [ -n "$FC_SERIAL" ]; then
		t "[ -e /dev/$FC_SERIAL ]" "FC device /dev/$FC_SERIAL present" "check USB cable / dmesg (wfb-ng will not start without it)"
	else
		t 'systemctl is-active --quiet bench-fc' "bench-fc running" "sudo systemctl start bench-fc"
	fi
else
	note "link state:    wfb-cli gs"
	note "MAVLink + RC:  $VENV/bin/python $BENCH_DIR/gs_mav.py --rc off     (then --rc sweep)"
	note "video on HDMI: sudo systemctl start bench-video-rx   (or ./video-rx.sh)"
fi
exit $rc
