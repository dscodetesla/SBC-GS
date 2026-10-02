# shared setup for gsmenu.sh cases: `gsmenu.sh <get|set|values|button|search> <args...>` (one subcommand per run)
# gsmenu.sh is run standalone with arguments (like PixelPilot's gsmenu does); the exit status is part of the golden.
script_under_test=gs/gsmenu.sh
invocation=standalone_wait
dump_baseline=0
rewrite_extra=(/dev/shm)

# gm_shim NAME: write a shim from stdin; it logs its argv first, so the call shows up in the golden
gm_shim() {
	{ printf '#!/bin/bash\necho "%s $*" >> "$SHIM_LOG"\n' "$1"; cat; } > "$ROOT/shims/$1"; chmod +x "$ROOT/shims/$1"
}

# gm_sb [iface]: base sandbox. iface = onboard wifi interface the board profile resolves to (default wifi0).
# The sandbox interface directory (/sys/class/net/<iface>) is NOT created here: cases call gm_iface to make it present.
gm_sb() {
	local iface="${1:-wifi0}" n
	mkdir -p "$ROOT/tmp/gsmenu_cache" "$ROOT/run" "$ROOT/dev/shm" "$ROOT/etc/default" "$ROOT/sys/class/net"
	# a different name than the shipped wifi0 proves the script follows $WIFI_IFACE instead of a literal
	sed -i "s/^WIFI_ONBOARD_IFACE=.*/WIFI_ONBOARD_IFACE='$iface'/" "$ROOT/gs/boards/radxa-zero3/board.conf"
	# tools that may be missing on the CI runner or would touch network/hardware: log-only
	for n in sshpass git killall msposd gsmenu.sh; do printf '' | gm_shim "$n"; done
	# nmcli: `connection show` answers from fixture files (active list, full list, secrets)
	gm_shim nmcli <<'SH'
case "$*" in
*"connection show"*)
	case "$*" in
	*--show-secrets*) cat "$SHIM_ROOT/nmcli.secrets" 2>/dev/null ;;
	*--active*) cat "$SHIM_ROOT/nmcli.active" 2>/dev/null ;;
	*) cat "$SHIM_ROOT/nmcli.all" 2>/dev/null ;;
	esac ;;
esac
exit 0
SH
	# ip -4 addr show <dev>: the address line carries the device name, so a wrong device is visible
	gm_shim ip <<'SH'
echo "    inet 10.9.8.7/24 brd 10.9.8.255 scope global $4"
SH
	# iw list: fixed channel table
	gm_shim iw <<'SH'
cat <<'EOT'
			* 5180 MHz [36] (23.0 dBm)
			* 5200 MHz [40] (23.0 dBm)
			* 5745 MHz [149] (14.0 dBm)
			* 5765 MHz [153] (disabled)
			* 2412 MHz [1] (20.0 dBm)
EOT
SH
	# yaml-cli -i FILE -g KEY: value from $SHIM_ROOT/yaml.values ("KEY value" lines)
	gm_shim yaml-cli <<'SH'
awk -v k="$4" '$1 == k { $1 = ""; sub(/^ /, ""); print }' "$SHIM_ROOT/yaml.values" 2>/dev/null
SH
	# jq: canned results (txpower decode / drm mode list / current mode)
	gm_shim jq <<'SH'
cat > /dev/null   # drain the pipe like the real jq (no SIGPIPE for the producer)
case "$*" in
*to_entries*) echo "wlan1 $(cat "$SHIM_ROOT/jq.power" 2>/dev/null || echo -1500)" ;;
*modes*) printf '1920x1080@60\n1280x720@60\n1920x1080@60\n' ;;
*) echo "1920x1080@60" ;;
esac
SH
	gm_shim drm_info <<'SH'
echo '{}'
SH
	gm_shim udevadm <<'SH'
echo "E: ID_NET_DRIVER=$(cat "$SHIM_ROOT/udev.driver" 2>/dev/null || echo rtl88xxau_wfb)"
SH
	# cache that does not need a refresh unless a case removes it
	echo 99999999999 > "$ROOT/tmp/gsmenu_cache/last_refresh"
	printf 'fec_k=8\nmcs=3\nosd_level=2\n' > "$ROOT/tmp/gsmenu_cache/alink.conf"
	printf '.image.mirror true\n.image.flip false\n.image.contrast 55\n.records.enabled true\n.isp.sensorConfig /etc/sensors/imx415_fpv.bin\n.fpv.enabled false\n.video0.size 1920x1080\n.wireless.txpower 25\n.wireless.channel 149\n.broadcast.stbc 1\n' > "$ROOT/yaml.values"
}

# gm_iface [name]: the wifi interface exists in the sandbox sysfs
gm_iface() { mkdir -p "$ROOT/sys/class/net/${1:-wifi0}"; }
# NetworkManager fixtures (nmcli -t connection show [--active] output)
gm_nm_active() { printf '%s\n' "$@" > "$ROOT/nmcli.active"; }
gm_nm_all()    { printf '%s\n' "$@" > "$ROOT/nmcli.all"; }
gm_nm_secrets(){ printf '%s\n' "$@" > "$ROOT/nmcli.secrets"; }
