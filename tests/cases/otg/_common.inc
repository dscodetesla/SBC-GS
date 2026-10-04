# shared setup for otg-gadget.sh cases: fake debugfs role switch, configfs root, udc, /proc bits
script_under_test=gs/otg-gadget.sh
invocation=standalone
dump_baseline=0
dump_files=(sys/kernel/debug/usb/fcc00000.dwc3/mode)
dump_trees=(sys/kernel/config)
otg_sb() {   # otg_sb <role mode: host|device>
	mkdir -p "$ROOT/sys/kernel/debug/usb/fcc00000.dwc3" "$ROOT/sys/class/udc/fcc00000.dwc3" \
	         "$ROOT/sys/kernel/config/usb_gadget" "$ROOT/proc/device-tree"
	echo "$1" > "$ROOT/sys/kernel/debug/usb/fcc00000.dwc3/mode"
	: > "$ROOT/proc/mounts"
	echo "0123456789abcdef" > "$ROOT/proc/device-tree/serial-number"
}
