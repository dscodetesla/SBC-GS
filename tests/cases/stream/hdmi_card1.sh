. "$HERE/cases/stream/_common.inc"
# the HDMI connector is card1 (a Pi 4/5 with the vc4 KMS driver), there is no card0: the player must start without waiting
case_setup() {
	stream_sb
	rm -r "$ROOT/sys/class/drm/card0-HDMI-A-1"
	mkdir -p "$ROOT/sys/class/drm/card1-HDMI-A-2"
	echo connected > "$ROOT/sys/class/drm/card1-HDMI-A-2/status"
}
