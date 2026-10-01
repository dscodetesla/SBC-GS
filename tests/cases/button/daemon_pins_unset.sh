. "$HERE/cases/button/_common.inc"
# q2/q3 pins empty -> "[ -z pin ] && exit 0" before any gpio access
script_args=()
case_setup() {
	button_sb host
	conf_set btn_q2_pin ''; conf_set btn_q3_pin ''
}
