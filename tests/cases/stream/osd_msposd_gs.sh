. "$HERE/cases/stream/_common.inc"
case_setup() {
	stream_sb
	conf_set osd_type msposd_gs
	conf_set osd_enable yes
	: > "$ROOT/dev/shm/msposd"; mkdir -p "$ROOT/sys/class/net/gs-wfb"
}
