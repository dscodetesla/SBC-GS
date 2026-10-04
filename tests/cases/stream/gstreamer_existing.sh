. "$HERE/cases/stream/_common.inc"
case_setup() {
	stream_sb
	conf_set video_player gstreamer
	conf_set video_codec h264
	conf_set osd_enable yes
	: > "$ROOT/Videos/1000.mkv"; : > "$ROOT/Videos/1001.mkv"
}
