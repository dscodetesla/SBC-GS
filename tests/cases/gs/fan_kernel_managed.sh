. "$HERE/cases/gs/_common.inc"
# a board whose fan is driven by the kernel (Pi 5): gs.sh must not start gs/fan.sh, and with no OTG role switch must not break either
case_setup() {
	gs_sb
	conf_set fan_service_enable yes
	conf_set otg_mode device
	printf "FAN_KERNEL_MANAGED='yes'\nOTG_CONTROLLER='none'\n" >> "$ROOT/gs/boards/radxa-zero3/board.conf"
}
