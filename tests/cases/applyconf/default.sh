script_under_test=gs/gs-applyconf.sh
case_setup() {
	dtbo rk3566-hdmi-max-resolution-4k.dtbo.disabled; dtbo radxa-zero3-disabled-wireless.dtbo.disabled
	dtbo radxa-zero3-external-antenna.dtbo.disabled; dtbo rk3566-ina226-overlay.dtbo.disabled
	dtbo rk3568-pwm14-m0.dtbo; dtbo rk3568-uart3-m0.dtbo; dtbo rk3568-i2c4-m0.dtbo.disabled; dtbo rk3568-spi3-m1-cs0-spidev.dtbo
}
