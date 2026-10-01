script_under_test=gs/gs-applyconf.sh
case_setup() {
	conf_set disable_integrated_wifi yes; dtbo radxa-zero3-disabled-wireless.dtbo.disabled
}
