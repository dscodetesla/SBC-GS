. "$HERE/cases/stream/_common.inc"
case_setup() {
	stream_sb
	conf_set record_on boot
	echo 5000000 > "$ROOT/df.avail_kb"
}
