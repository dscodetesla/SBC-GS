. "$HERE/cases/gsmenu/_common.inc"
sort_shim_log=1
script_args=(set gs system gs_rendering on)
case_setup() {
	gm_sb
	touch "$ROOT/dev/shm/msposd"; conf_set msposd_gs_record yes
}
