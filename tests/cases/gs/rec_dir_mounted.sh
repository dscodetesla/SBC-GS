. "$HERE/cases/gs/_common.inc"
case_setup() {
	gs_sb
	printf "/dev/mmcblk0p4 %s exfat rw 0 0\n" "$ROOT/Videos" > "$ROOT/proc/mounts"
}
