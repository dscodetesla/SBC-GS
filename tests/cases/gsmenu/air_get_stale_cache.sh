. "$HERE/cases/gsmenu/_common.inc"
script_args=(get air camera contrast)
case_setup() {
	gm_sb
	rm "$ROOT/tmp/gsmenu_cache/last_refresh"
}
