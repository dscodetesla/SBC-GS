. "$HERE/cases/gs/_common.inc"
case_setup() {
	gs_sb
	conf_set ttyd_enable yes; mkdir -p "$ROOT/enabled"; : > "$ROOT/enabled/ttyd"
}
