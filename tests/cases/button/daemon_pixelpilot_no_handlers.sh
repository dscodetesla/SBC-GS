. "$HERE/cases/button/_common.inc"
# daemon part (no function argument): pixelpilot owns the d-pad, q2/q3 have no handlers -> every loop exits at once
script_args=()
case_setup() {
	button_sb host
	conf_set btn_q2_single_press ''; conf_set btn_q2_long_press ''
	conf_set btn_q3_single_press ''; conf_set btn_q3_long_press ''
}
