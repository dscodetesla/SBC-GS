. "$HERE/cases/stream/_common.inc"
# no monitor and hdmi_wait_timeout=2: gives up after 2 s and starts the player anyway (headless Pi); 0 = wait forever (monitor_wait)
case_setup() {
	stream_sb
	echo disconnected > "$ROOT/sys/class/drm/card0-HDMI-A-1/status"
	echo "hdmi_wait_timeout='2'" >> "$ROOT/etc/gs.conf"   # optional key, not in the default gs.conf
	sleep_limit=5
}
