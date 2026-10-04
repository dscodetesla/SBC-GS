. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs main WFB_NICS)
case_setup() {
	gm_sb
	printf "WFB_NICS=\"wlan1 wlan2\"\n" > "$ROOT/etc/default/wifibroadcast"
}
