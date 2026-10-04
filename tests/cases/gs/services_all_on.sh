. "$HERE/cases/gs/_common.inc"
case_setup() {
	gs_sb
	conf_set alink_enable yes; conf_set wfb_rtsp_server_enable yes; conf_set use_gps yes; conf_set wfb_outgoing_ip 192.168.9.5; conf_set use_external_rtc yes
}
