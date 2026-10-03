. "$HERE/cases/stream/_common.inc"
case_setup() {
	stream_sb
	conf_set osd_config_file /config/my_osd.json
	conf_set osd_widgets_osmon no
}
