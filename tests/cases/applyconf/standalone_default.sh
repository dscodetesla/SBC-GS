script_under_test=gs/gs-applyconf.sh
invocation=standalone   # as button.sh:138 runs it
case_setup() {
	dtbo rk3566-hdmi-max-resolution-4k.dtbo.disabled
}
