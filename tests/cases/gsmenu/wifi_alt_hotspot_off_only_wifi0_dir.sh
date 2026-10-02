. "$HERE/cases/gsmenu/_common.inc"
script_args=(set gs wifi hotspot off)
case_setup() {
	gm_sb wlan9
	gm_iface wifi0; gm_nm_all "Home  u2  wifi" "hotspot  u1  wifi"
}
