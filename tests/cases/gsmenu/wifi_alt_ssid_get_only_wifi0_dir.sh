. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs wifi ssid)
case_setup() {
	gm_sb wlan9
	gm_iface wifi0; gm_nm_active "Home:u2:802-11-wireless:wlan9" "Other:u4:802-11-wireless:wifi0"
}
