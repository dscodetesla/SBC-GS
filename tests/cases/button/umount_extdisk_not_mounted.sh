. "$HERE/cases/button/_common.inc"
script_args=(ummount_extdisk)
case_setup() {
	button_sb host; mkdir -p "$ROOT/proc"; : > "$ROOT/proc/mounts"
}
