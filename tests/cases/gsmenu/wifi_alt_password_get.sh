. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs wifi password)
case_setup() {
	gm_sb wlan9
	gm_iface wlan9; gm_nm_active "Home:u2:802-11-wireless:wlan9" "Other:u4:802-11-wireless:wifi0"; gm_nm_secrets 802-11-wireless-security.psk:alt-pass
}
