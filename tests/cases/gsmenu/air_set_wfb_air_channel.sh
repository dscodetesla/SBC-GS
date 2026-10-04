. "$HERE/cases/gsmenu/_common.inc"
dump_files=(etc/wifibroadcast.cfg)
script_args=(set air wfbng air_channel "149 (5745 MHz)")
case_setup() {
	gm_sb
	printf "[common]\nwifi_channel = 36\n" > "$ROOT/etc/wifibroadcast.cfg"
}
