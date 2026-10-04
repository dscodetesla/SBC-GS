. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs system gs_rendering)
case_setup() {
	gm_sb
	conf_set osd_type msposd_gs
}
