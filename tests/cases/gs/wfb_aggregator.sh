. "$HERE/cases/gs/_common.inc"
case_setup() {
	gs_sb
	conf_set wfb_mode aggregator; conf_set wfb_integrated_wnic wifi0; mkdir -p "$ROOT/sys/class/net/wifi0"
}
