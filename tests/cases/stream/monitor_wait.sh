. "$HERE/cases/stream/_common.inc"
case_setup() {
	stream_sb
	echo disconnected > "$ROOT/sys/class/drm/card0-HDMI-A-1/status"
	sleep_limit=3
}
