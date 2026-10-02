. "$HERE/cases/gsmenu/_common.inc"
script_args=(set gs wifi wlan on Home pass1)
case_setup() {
	gm_sb wlan9
	gm_iface wlan9; gm_nm_all "Home  u2  wifi" "hotspot  u1  wifi"
}
