. "$HERE/cases/button/_common.inc"
script_args=(ummount_extdisk)
case_setup() {
	# artificial line with a double slash: only this shape reaches the umount branch (keeps that branch covered)
	button_sb host; printf '/dev/sda1 /%s ext4 rw 0 0\n' "$ROOT/Videos" > "$ROOT/proc/mounts"
}
