. "$HERE/cases/button/_common.inc"
script_args=(ummount_extdisk)
case_setup() {
	# realistic /proc/mounts line (single slash). The script greps "^/dev/sda1 /${rec_dir}" = double slash,
	# so it does NOT match and prints "extdisk already umounted" although the disk is mounted (latent bug, kept as-is).
	button_sb host; printf '/dev/sda1 %s exfat rw,relatime 0 0\n' "$ROOT/Videos" > "$ROOT/proc/mounts"
}
