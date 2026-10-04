. "$HERE/cases/button/_common.inc"
sort_shim_log=1   # background otg-gadget.sh and the LED blinker log concurrently
script_args=(change_otg_mode)
case_setup() {
	button_sb host; gpioset_limit=3; mkdir -p "$ROOT/sys/kernel/config/usb_gadget/g1"
}
