. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs wfbng adaptivelink)
case_setup() {
	gm_sb
	conf_set alink_enable yes
}
