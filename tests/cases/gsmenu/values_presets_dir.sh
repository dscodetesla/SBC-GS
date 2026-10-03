. "$HERE/cases/gsmenu/_common.inc"
script_args=(values air presets preset)
case_setup() {
	gm_sb
	mkdir -p "$ROOT/etc/presets/presets/alpha" "$ROOT/etc/presets/presets/beta"
}
