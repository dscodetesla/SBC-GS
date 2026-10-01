. "$HERE/cases/init/_common.inc"
case_setup() {
	init_sb
	printf "/dev/mmcblk0p5 %s exfat defaults,nofail 0 0\n" "$ROOT/Videos" > "$ROOT/media/root-ro/etc/fstab"
}
