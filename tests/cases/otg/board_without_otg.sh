. "$HERE/cases/otg/_common.inc"
# a board without a runtime OTG role switch (Pi 5/Pi 4: OTG_CONTROLLER='none'): the gadget script must exit 0 and touch nothing
case_setup() {
	otg_sb host
	sed -i "s/^OTG_CONTROLLER=.*/OTG_CONTROLLER='none'/" "$ROOT/gs/boards/radxa-zero3/board.conf"
}
