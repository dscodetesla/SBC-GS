# shared setup for button.sh function-dispatch cases: `button.sh <function> [arg]` runs one function and exits
script_under_test=gs/button.sh
invocation=standalone_wait
dump_baseline=0
dump_files=(run/pixelpilot.msg sys/kernel/debug/usb/fcc00000.dwc3/mode run/record_button.fifo.out)
button_sb() {
	mkdir -p "$ROOT/etc/systemd/system/multi-user.target.wants" "$ROOT/run" "$ROOT/proc" "$ROOT/sys/kernel/debug/usb/fcc00000.dwc3" "$ROOT/sys/class/udc/fcc00000.dwc3"
	: > "$ROOT/etc/systemd/system/multi-user.target.wants/gs.service"     # the script exits at once without it
	echo "${1:-host}" > "$ROOT/sys/kernel/debug/usb/fcc00000.dwc3/mode"
	conf_set rec_dir "$ROOT/Videos"; mkdir -p "$ROOT/Videos"
}
