# shared setup for gs-init.sh (first-boot initialisation: partitions, network/samba/wfb/gpsd config generation)
script_under_test=gs/gs-init.sh
invocation=standalone          # systemd runs it as its own process; it sources gs.conf itself
rewrite_console=1              # it tees to /dev/ttyFIQ0 and /dev/tty1
dump_baseline=0
sleep_limit=0
dump_files=(etc/wifibroadcast.cfg etc/default/gpsd etc/samba/smb.conf media/root-ro/etc/fstab etc/network/interfaces.d/radxa0 dev/ttyFIQ0)
dump_trees=(etc/systemd/network)
init_sb() {
	mkdir -p "$ROOT/dev"; : > "$ROOT/dev/ttyFIQ0"   # the Rockchip debug console exists on the Radxa image (gs-init.sh skips a missing one)
	sed -i "s#^CONSOLE_TTY=.*#CONSOLE_TTY='$ROOT/dev/ttyFIQ0'#" "$ROOT/gs/boards/radxa-zero3/board.conf"   # keep the console off the real /dev
	mkdir -p "$ROOT/etc/systemd/network" "$ROOT/etc/network/interfaces.d" "$ROOT/etc/samba" "$ROOT/media/root-ro/etc" "$ROOT/config" "$ROOT/gs"
	echo gs-test-host > "$ROOT/etc/hostname"
	printf '[global]\n' > "$ROOT/etc/samba/smb.conf"
	: > "$ROOT/media/root-ro/etc/fstab"
	conf_set rec_dir "$ROOT/Videos"
}
