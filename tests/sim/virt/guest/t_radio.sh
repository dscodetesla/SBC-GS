#!/bin/bash
# Guest test: radio hotplug. mac80211_hwsim radios are created/destroyed at runtime over generic netlink (hwsimctl); the REAL systemd-udevd runs
# the project's 99-GS.rules and the REAL gs/wfb.sh. Only systemd-run/systemctl are shims (no systemd here); ip, iw, wfb_rx are real.
. /virt/lib.sh
mod_load mac80211_hwsim radios=0 || { summary radio; exit 1; }
setup_etc
mkdir -p /etc/default /run/systemd/units /usr/local/bin
ln -sf /dev/null /run/systemd/units/invocation:gs.service      # wfb.sh only runs "after gs.service started"
cat >> /etc/gs.conf <<'CONF'
gs_enable='yes'
fpv_firmware_type='wfb'
wfb_mode='standalone'
wfb_integrated_wnic=''
wfb_txpower=''
CONF
cat > /usr/local/bin/systemctl <<'SH'
#!/bin/sh
echo "systemctl $*" >> /run/shim.log
SH
cat > /usr/local/bin/systemd-run <<'SH'
#!/bin/sh
# log, then run the payload (what systemd-run would start as a transient unit) in the background
echo "systemd-run $*" >> /run/shim.log
while [ "${1#--}" != "$1" ]; do shift; done
setsid "$@" >/tmp/shim-run.$$.log 2>&1 &
SH
chmod +x /usr/local/bin/systemctl /usr/local/bin/systemd-run
: > /run/shim.log
start_udevd || { summary radio; exit 1; }

wfb_nics() { sed -n 's/^WFB_NICS="\(.*\)"$/\1/p' /etc/default/wifibroadcast 2>/dev/null; }
nics_is() { [ "$(wfb_nics)" = "$1" ]; }
ifaces() { local i; for i in /sys/class/net/wl* /sys/class/net/hwsim*; do [ -e "$i" ] && printf '%s ' "${i##*/}"; done; }
restarts() { grep -c 'systemctl restart wifibroadcast@gs' /run/shim.log; }

# ---- add radios (standalone mode: wfb.sh rewrites WFB_NICS and restarts wifibroadcast@gs) ----
hwsimctl new >/tmp/new0.out 2>&1; check radio.new.0 "$(cat /tmp/new0.out)" grep -q 'rc=0' /tmp/new0.out
check radio.add.wlan0.iface "ifaces: $(ifaces)" wait_for 10 test -d /sys/class/net/wlan0
check radio.add.wlan0.rule-fired "udevd never ran /gs/wfb.sh wlan0" wait_for 15 runs_ge '/gs/wfb.sh wlan0' 1
check radio.add.wlan0.wfb_nics "WFB_NICS='$(wfb_nics)'" wait_for 15 nics_is wlan0
hwsimctl new >/tmp/new1.out 2>&1
check radio.add.wlan1.iface "ifaces: $(ifaces)" wait_for 10 test -d /sys/class/net/wlan1
sleep 3; udevadm settle --timeout=20
# FINDING (real udevd, 98-rename.rules:2 renames EVERY wl* to wlan0): the second adapter's rename fails (name busy) and udev drops the whole
# event, so 99-GS.rules never runs /gs/wfb.sh for it
info "udevd wlan1: $(grep wlan1 /tmp/udevd.log | grep -E 'Failed to rename|Failed to process' | cut -c1-140 | tr '\n' '|')"
eq radio.add.wlan1.second-adapter.rule-NOT-fired 0 "$(runs_of '/gs/wfb.sh wlan1')"
check radio.add.wlan1.rename-failed "no 'Failed to process device' in udevd log" grep -q "wlan1: Failed to process device, ignoring: File exists" /tmp/udevd.log
nics_is wlan0; eq radio.add.wlan1.wfb_nics-unchanged wlan0 "$(wfb_nics)"
info "wfb.sh runs so far: $(grep -o 'Running command "/gs/wfb.sh[^"]*"' /tmp/udevd.log | tr '\n' ' ')"
eq radio.rule.no-hwsim0 0 "$(runs_of '/gs/wfb.sh hwsim0')"
check radio.add.restarts "restarts=$(restarts)" test "$(restarts)" -ge 1

# ---- remove radio 1 (remove event -> /gs/wfb.sh without arguments) ----
R_BEFORE="$(grep -c 'command "/gs/wfb.sh"$' /tmp/udevd.log)"
hwsimctl del 1 >/tmp/del1.out 2>&1; check radio.del.1 "$(cat /tmp/del1.out)" grep -q 'rc=0' /tmp/del1.out
check radio.remove.wlan1-gone "ifaces: $(ifaces)" wait_for 10 test ! -d /sys/class/net/wlan1
check radio.remove.rule-fired "remove rule did not run /gs/wfb.sh without args" wait_for 15 noarg_runs_gt "$R_BEFORE"
check radio.remove.wfb_nics "WFB_NICS='$(wfb_nics)'" wait_for 15 nics_is wlan0
info "wifibroadcast.cfg [common]: $(grep -A2 '^\[common\]' /etc/wifibroadcast.cfg | tr '\n' ' ')"

# ---- remove the last adapter: wfb.sh exits early on an empty NIC list ([ -z "$wfb_nics" ] && exit 0): stale WFB_NICS, no restart ----
RS="$(restarts)"; R_BEFORE="$(grep -c 'command "/gs/wfb.sh"$' /tmp/udevd.log)"
hwsimctl del 0 >/tmp/del0.out 2>&1
check radio.remove.wlan0-gone "ifaces: $(ifaces)" wait_for 10 test ! -d /sys/class/net/wlan0
check radio.remove.last.rule-fired "no run" wait_for 15 noarg_runs_gt "$R_BEFORE"
sleep 1
eq radio.remove.last.stale-WFB_NICS wlan0 "$(wfb_nics)"
eq radio.remove.last.no-restart "$RS" "$(restarts)"

# ---- aggregator mode: add event with $1 -> monitor_wnic (real ip/iw) + wfb_rx per stream (name wlan0 is free again) ----
echo "wfb_mode='aggregator'" >> /etc/gs.conf
: > /run/shim.log
hwsimctl new >/tmp/new2.out 2>&1
check radio.agg.iface "ifaces: $(ifaces)" wait_for 10 test -d /sys/class/net/wlan0
check radio.agg.rule-fired "wfb.sh runs: $(grep -c 'wfb.sh wlan0' /tmp/udevd.log)" wait_for 15 runs_ge '/gs/wfb.sh wlan0' 2
check radio.agg.monitor-mode "iw: $(iw dev wlan0 info 2>&1 | grep type)" wait_for 20 sh -c 'iw dev wlan0 info | grep -q "type monitor"'
check radio.agg.channel-161 "iw: $(iw dev wlan0 info | grep channel)" wait_for 10 sh -c 'iw dev wlan0 info | grep -q "channel 161"'
check radio.agg.systemd-run-video "shim: $(tr '\n' '|' < /run/shim.log)" wait_for 10 grep -q 'systemd-run /usr/bin/wfb_rx -f -p 0 -c 127.0.0.1 -u 10000 -i 7669206 wlan0' /run/shim.log
check radio.agg.systemd-run-mavlink "shim: $(tr '\n' '|' < /run/shim.log)" grep -q 'systemd-run /usr/bin/wfb_rx -f -p 16 -c 127.0.0.1 -u 10001 -i 7669206 wlan0' /run/shim.log
check radio.agg.wfb_rx-running "no wfb_rx process: $(cat /tmp/shim-run.*.log 2>/dev/null | head -3 | tr '\n' '|')" wait_for 10 pgrep -f 'wfb_rx.*wlan0'
info "wfb_rx processes on wlan0: $(pgrep -fc 'wfb_rx.*wlan0')"
hwsimctl del 2 >/tmp/del2.out 2>&1
check radio.agg.remove.wlan0-gone "ifaces: $(ifaces)" wait_for 10 test ! -d /sys/class/net/wlan0
if wait_for 10 sh -c '! pgrep -f "wfb_rx.*wlan0" >/dev/null'; then pass radio.agg.remove.wfb_rx-exits "wfb_rx exited after its radio vanished"; else info "OBSERVED: wfb_rx still running 10 s after its radio was removed (no cleanup by the remove rule)"; fi
summary radio
