. "$HERE/cases/button/_common.inc"
script_args=(toggle_record)
case_setup() {
	button_sb host; mkfifo "$ROOT/run/record_button.fifo"; ( timeout 10 cat "$ROOT/run/record_button.fifo" > "$ROOT/run/record_button.fifo.out" ) &
}
