. "$HERE/cases/button/_common.inc"
script_args=(change_wifi_mode)
case_setup() {
	button_sb host; mkdir -p "$ROOT/sys/class/net/wifi0"; echo "wifi0 wifi connected other" > "$ROOT/nmcli.status"
}
