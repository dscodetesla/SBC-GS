. "$HERE/cases/button/_common.inc"
script_args=(mount_extdisk /dev/mmcblk1p1)
case_setup() {
	button_sb host; echo /dev/mmcblk1p3 > "$ROOT/findmnt.source"
}
