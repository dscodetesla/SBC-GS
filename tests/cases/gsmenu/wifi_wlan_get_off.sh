. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs wifi wlan)
case_setup() {
	gm_sb 
	gm_iface; gm_nm_active "hotspot:u1:802-11-wireless:wifi0"
}
