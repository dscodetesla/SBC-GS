script_under_test=gs/gs-applyconf.sh
case_setup() {
	printf "[Address]\nAddress=192.168.1.20/24\nAddress=10.0.36.254/24\n" > "$ROOT/etc/systemd/network/br0.network"
	conf_set br0_fixed_ip 192.168.9.20/24; printf "iface radxa0 inet static\n        address 192.168.2.20/24\n" > "$ROOT/etc/network/interfaces.d/radxa0"
}
