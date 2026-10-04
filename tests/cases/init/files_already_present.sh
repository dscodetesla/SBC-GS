. "$HERE/cases/init/_common.inc"
case_setup() {
	init_sb
	for f in br0.netdev br0.network eth0.network eth1.network usb0.network dummy0.netdev dummy0.network; do echo "keep $f" > "$ROOT/etc/systemd/network/$f"; done
	echo "keep radxa0" > "$ROOT/etc/network/interfaces.d/radxa0"; printf "[global]\n[config]\n   path = /keep\n" > "$ROOT/etc/samba/smb.conf"
}
