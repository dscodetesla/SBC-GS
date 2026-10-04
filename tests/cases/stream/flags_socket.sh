. "$HERE/cases/stream/_common.inc"
case_setup() {
	stream_sb
	conf_set wfb_outgoing_video socket
	conf_set disable_vsync yes
	conf_set dvr_fmp4 no
	conf_set gsmenu_enable no
}
