. "$HERE/cases/stream/_common.inc"
case_setup() {
	stream_sb
	conf_set video_player gstreamer
	conf_set video_codec h265
}
