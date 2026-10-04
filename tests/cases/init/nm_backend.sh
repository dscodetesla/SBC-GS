. "$HERE/cases/init/_common.inc"
# Raspberry Pi profile shape (NetworkManager owns the network, MBR card, config.txt overlays, no OTG role switch): br0 goes through nmcli,
# no networkd files, no sgdisk/dtc, no radxa0 gadget block
case_setup() {
	init_sb
	conf="$ROOT/gs/boards/radxa-zero3/board.conf"
	sed -i "s/^OTG_CONTROLLER=.*/OTG_CONTROLLER='none'/" "$conf"
	printf "%s\n" "NET_BACKEND='networkmanager'" "PART_TABLE='mbr'" "DTBO_MODE='config-txt'" >> "$conf"
}
