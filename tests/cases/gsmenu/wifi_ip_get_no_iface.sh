. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs wifi IP)
case_setup() {
	gm_sb 
	gm_nm_active "Home:u2:802-11-wireless:wifi0" "Wired:u3:802-3-ethernet:eth0"
}
