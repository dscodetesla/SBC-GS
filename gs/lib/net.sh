#!/bin/bash
# Network helpers for boards where NetworkManager owns the network (NET_BACKEND=networkmanager, Raspberry Pi OS Bookworm).
# Usage: . /gs/lib/net.sh; gs_net_nm_bridge br0 192.168.1.20/24 10.0.36.254/24 eth0 eth1 usb0
# On Radxa gs-init.sh writes systemd-networkd files instead (docs/PI-PORT.md). Idempotent: a connection that exists is not added again.
# Everything is nmcli with fixed arguments; the names and addresses come from gs.conf (br0_fixed_ip, br0_fixed_ip2) and are validated here.

_gs_net_valid_if() { [[ "$1" =~ ^[A-Za-z0-9._-]{1,15}$ ]]; }
_gs_net_valid_cidr() { [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]]; }

# gs_net_nm_wait [seconds]: wait until NetworkManager answers (it may start after gs-init at the first boot); returns 1 when it never does
gs_net_nm_wait() {
	local n="${1:-30}"
	while [ "$n" -gt 0 ]; do
		nmcli -t general status >/dev/null 2>&1 && return 0
		sleep 1
		n=$((n - 1))
	done
	return 1
}

_gs_net_has_con() { nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$1"; }

# gs_net_nm_bridge BRIDGE IP1 IP2 PORT...: bridge with a DHCP address plus two fixed addresses (as the networkd br0.network of the Radxa
# image) and the ports (a dummy0 member keeps br0 up without a cable, as on Radxa). A port that does not exist yet is fine: the
# connection is bound to the interface name and applies when the device appears (USB NICs).
gs_net_nm_bridge() {
	local br="$1" ip1="$2" ip2="$3" port
	shift 3
	_gs_net_valid_if "$br" && _gs_net_valid_cidr "$ip1" && _gs_net_valid_cidr "$ip2" || { echo "net.sh: invalid bridge name or address: '$br' '$ip1' '$ip2'" >&2; return 1; }
	gs_net_nm_wait 30 || { echo "net.sh: NetworkManager does not answer: br0 not configured (re-run gs-init or add it by hand)" >&2; return 1; }
	if ! _gs_net_has_con "$br"; then
		nmcli connection add type bridge ifname "$br" con-name "$br" bridge.stp no \
			ipv4.method auto ipv4.may-fail yes ipv4.addresses "$ip1,$ip2" ipv6.method ignore || return 1
	fi
	for port in "$@" dummy0; do
		_gs_net_valid_if "$port" || { echo "net.sh: invalid port name '$port'" >&2; return 1; }
		_gs_net_has_con "$br-$port" && continue
		if [ "$port" = dummy0 ]; then
			nmcli connection add type dummy ifname dummy0 con-name "$br-$port" master "$br" slave-type bridge || return 1
		else
			nmcli connection add type bridge-slave ifname "$port" con-name "$br-$port" master "$br" || return 1
		fi
	done
	nmcli connection up "$br" >/dev/null 2>&1 || true
}
