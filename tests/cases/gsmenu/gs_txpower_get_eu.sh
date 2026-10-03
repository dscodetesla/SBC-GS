. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs wfbng txpower)
case_setup() {
	gm_sb
	echo "wifi_txpower = {\"wlan1\": 1900}" > "$ROOT/etc/wifibroadcast.cfg"; echo 1900 > "$ROOT/jq.power"; echo rtl88x2eu > "$ROOT/udev.driver"
}
