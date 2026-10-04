. "$HERE/cases/otg/_common.inc"
case_setup() {
	# fake tree: validates control flow and shim calls only, NOT real configfs semantics
	otg_sb device
	g="$ROOT/sys/kernel/config/usb_gadget/g1"; mkdir -p "$g/configs/c.1/strings/0x409" "$g/strings/0x409" "$g/functions/ncm.usb0"
	echo fcc00000.dwc3 > "$g/UDC"; ln -s ../../functions/ncm.usb0 "$g/configs/c.1/ncm.usb0"
}
