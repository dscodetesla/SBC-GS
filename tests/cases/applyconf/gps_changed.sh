script_under_test=gs/gs-applyconf.sh
case_setup() {
	conf_set gps_uart ttyS4; conf_set gps_uart_baudrate 115200
}
