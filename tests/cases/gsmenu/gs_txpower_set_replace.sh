. "$HERE/cases/gsmenu/_common.inc"
dump_files=(etc/wifibroadcast.cfg)
script_args=(set gs wfbng txpower 80)
case_setup() {
	gm_sb
	printf "WFB_NICS=\"wlan1\"\n" > "$ROOT/etc/default/wifibroadcast"; printf "[common]\nwifi_txpower = {\"wlan1\": 0}\n" > "$ROOT/etc/wifibroadcast.cfg"; echo rtl88x2eu > "$ROOT/udev.driver"
}
