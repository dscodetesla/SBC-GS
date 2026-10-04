. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs wfbng txpower)
case_setup() {
	gm_sb
	echo "wifi_txpower = {\"wlan1\": -1500}" > "$ROOT/etc/wifibroadcast.cfg"
}
