. "$HERE/cases/stream/_common.inc"
case_setup() {
	stream_sb
	conf_set record_on boot
	echo 500000 > "$ROOT/df.avail_kb"
}
