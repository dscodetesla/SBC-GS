script_under_test=gs/gs-applyconf.sh
case_setup() {
	conf_set enable_external_antenna yes; dtbo radxa-zero3-external-antenna.dtbo.disabled; mkdir -p "$ROOT/sys/class/net/wifi0"
}
