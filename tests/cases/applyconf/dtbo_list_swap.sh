script_under_test=gs/gs-applyconf.sh
case_setup() {
	conf_set dtbo_enable_list "uart3-m0 i2c5-m0"; dtbo rk3568-pwm14-m0.dtbo; dtbo rk3568-uart3-m0.dtbo; dtbo rk3568-i2c5-m0.dtbo.disabled
}
