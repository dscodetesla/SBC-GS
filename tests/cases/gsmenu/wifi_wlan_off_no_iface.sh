. "$HERE/cases/gsmenu/_common.inc"
script_args=(set gs wifi wlan off Home)
case_setup() {
	gm_sb 
	gm_nm_all "Home  u2  wifi" "hotspot  u1  wifi"
}
