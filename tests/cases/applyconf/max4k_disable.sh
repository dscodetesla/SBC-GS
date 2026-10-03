script_under_test=gs/gs-applyconf.sh
case_setup() {
	conf_set max_resolution_4k no; dtbo rk3566-hdmi-max-resolution-4k.dtbo
}
