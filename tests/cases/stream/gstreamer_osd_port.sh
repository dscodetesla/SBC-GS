. "$HERE/cases/stream/_common.inc"
# the OSD listens on the MAVLink port of gs.conf (osd_mavlink_port), not on a hard-coded 14550
case_setup() {
	stream_sb
	conf_set video_player gstreamer
	conf_set video_codec h265
	echo "osd_mavlink_port='14560'" >> "$ROOT/etc/gs.conf"   # optional key, not in the default gs.conf
}
