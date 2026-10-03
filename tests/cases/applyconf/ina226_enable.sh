script_under_test=gs/gs-applyconf.sh
case_setup() {
	conf_set ina226_kernel_driver yes; dtbo rk3566-ina226-overlay.dtbo.disabled
}
