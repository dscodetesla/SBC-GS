. "$HERE/cases/button/_common.inc"
script_args=(change_otg_mode)
case_setup() {
	button_sb host; gpioset_limit=3   # LED blinker loop ends at the 3rd gpioset call
}
