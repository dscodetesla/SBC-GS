script_under_test=gs/gs-applyconf.sh
case_setup() {
	conf_set btn_pin_layout bonnet; printf "%s\n" "wifi_ssid=MyNet" "gps_uart_baudrate=9600" > "$ROOT/config/custom.conf"
}
