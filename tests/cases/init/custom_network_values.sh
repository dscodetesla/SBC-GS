. "$HERE/cases/init/_common.inc"
case_setup() {
	init_sb
	conf_set br0_fixed_ip 10.1.2.3/24; conf_set br0_fixed_ip2 10.9.8.7/24; conf_set gadget_net_fixed_ip 192.168.77.20/24; conf_set wfb_channel 36; conf_set wfb_region DE; conf_set gps_uart ttyS1; conf_set gps_uart_baudrate 9600; conf_set rootfs_reserved_space 2048
}
