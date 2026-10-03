. "$HERE/cases/button/_common.inc"
script_args=(change_wifi_mode)
case_setup() {
	button_sb host; mkdir -p "$ROOT/sys/class/net/wifi0"; conf_set wfb_integrated_wnic wifi0
}
