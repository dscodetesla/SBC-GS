. "$HERE/cases/gs/_common.inc"
case_setup() {
	gs_sb
	conf_set fan_service_enable no; conf_set oled_enable no; conf_set webui_enable no; conf_set otg_mode host; conf_set ttyd_enable no
}
