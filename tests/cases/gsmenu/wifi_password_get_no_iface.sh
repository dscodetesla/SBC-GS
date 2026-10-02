. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs wifi password)
case_setup() {
	gm_sb 
	gm_nm_active "Home:u2:802-11-wireless:wifi0"; gm_nm_secrets "802-11-wireless-security.psk:s3cret"
}
