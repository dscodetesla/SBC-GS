. "$HERE/cases/gsmenu/_common.inc"
script_args=(set gs wifi wlan on NewNet pass2)
case_setup() {
	gm_sb 
	gm_iface; gm_nm_all "Home  u2  wifi" "hotspot  u1  wifi"
}
