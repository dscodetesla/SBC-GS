. "$HERE/cases/gsmenu/_common.inc"
sort_shim_log=1
script_args=(set gs wfbng gs_channel "149 (5745 MHz)")
case_setup() {
	gm_sb
	echo "GSMENU_VTX_DETECTED=0" >> "$ROOT/etc/gs.conf"
}
