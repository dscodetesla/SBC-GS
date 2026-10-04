script_under_test=gs/gs-applyconf.sh
case_setup() {
	printf "/dev/mmcblk0p5 %s exfat defaults,nofail 0 0\n" "$ROOT/Videos" > "$ROOT/media/root-ro/etc/fstab"; printf "[Videos]\n   %s\n" "$ROOT/Videos" > "$ROOT/etc/samba/smb.conf"
}
