. "$HERE/cases/gsmenu/_common.inc"
dump_files=(etc/wifibroadcast.cfg)
script_args=(set gs wfbng txpower 50)
case_setup() {
	gm_sb
	printf "WFB_NICS=\"wlan1 wlan2\"\n" > "$ROOT/etc/default/wifibroadcast"; printf "[common]\nlog_interval = 1000\n" > "$ROOT/etc/wifibroadcast.cfg"
}
