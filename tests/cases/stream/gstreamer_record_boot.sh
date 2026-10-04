. "$HERE/cases/stream/_common.inc"
case_setup() {
	stream_sb
	conf_set video_player gstreamer
	conf_set record_on boot
	conf_set osd_enable no
	: > "$ROOT/Videos/1000.mkv"; : > "$ROOT/Videos/1004.mkv"
}
