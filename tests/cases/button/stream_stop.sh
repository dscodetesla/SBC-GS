. "$HERE/cases/button/_common.inc"
script_args=(toggle_stream)
case_setup() {
	button_sb host; mkdir -p "$ROOT/run/systemd/units"; ln -s x "$ROOT/run/systemd/units/invocation:stream.service"
}
