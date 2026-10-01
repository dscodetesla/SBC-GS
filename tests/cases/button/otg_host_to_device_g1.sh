. "$HERE/cases/button/_common.inc"
script_args=(change_otg_mode)
case_setup() {
	button_sb host; gpioset_limit=3; mkdir -p "$ROOT/sys/kernel/config/usb_gadget/g1"
}
