#!/bin/bash
# Shared helpers for the guest test scripts (sourced). Output markers are parsed by tests/sim/virt/run.sh.
export PATH=/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin
NFAIL=0
pass() { echo "VIRT-PASS $1${2:+ :: $2}"; }
fail() { echo "VIRT-FAIL $1${2:+ :: $2}"; NFAIL=$((NFAIL + 1)); }
info() { echo "VIRT-INFO $*"; }
# check NAME DETAIL-ON-FAIL cmd...   (cmd runs in this shell)
check() { local n="$1" d="$2"; shift 2; if "$@"; then pass "$n"; else fail "$n" "$d"; fi; }
# eq NAME EXPECTED ACTUAL
eq() { if [ "$2" = "$3" ]; then pass "$1" "'$3'"; else fail "$1" "expected '$2', got '$3'"; fi; }
# mod_load NAME [param=value...]: insmod NAME's dependencies (order precomputed on the host by modprobe --show-depends), then NAME with params
mod_load() {
	local name="$1" f last; shift
	[ -f /mods/dep."$name" ] || { fail "modload.$name" "no dependency list for $name"; return 1; }
	last="$(tail -n 1 /mods/dep."$name")"
	while read -r f; do
		[ "$f" = "$last" ] && continue
		insmod "/mods/$f" 2>/dev/null
	done < /mods/dep."$name"
	if insmod "/mods/$last" "$@" 2>/tmp/insmod.err; then info "insmod $name $* ok"; else
		case "$(cat /tmp/insmod.err)" in *"File exists"*) info "insmod $name already loaded" ;; *) fail "modload.$name" "$(cat /tmp/insmod.err)"; return 1 ;; esac
	fi
}
# wait_for SECONDS cmd...: poll (every 0.1 s) until cmd succeeds
wait_for() { local n=$(( $1 * 10 )); shift; while [ "$n" -gt 0 ]; do "$@" && return 0; sleep 0.1; n=$((n - 1)); done; return 1; }
# Minimal /etc for the real udev and the real gs scripts
setup_etc() {
	mkdir -p /etc/udev/rules.d /run/udev /var/log
	cp /gs-etc/gs.conf /etc/gs.conf
}
summary() { echo "VIRT-SUMMARY $1 fails=$NFAIL"; }
# start_udevd: the REAL systemd-udevd (debug log -> /tmp/udevd.log) with the rules in /etc/udev/rules.d, then a coldplug
start_udevd() {
	mkdir -p /run/udev
	/usr/lib/systemd/systemd-udevd --debug >/tmp/udevd.log 2>&1 &
	wait_for 10 test -S /run/udev/control || { fail udevd.start "no control socket: $(tail -3 /tmp/udevd.log)"; return 1; }
	udevadm trigger --type=subsystems --action=add; udevadm trigger --type=devices --action=add
	udevadm settle --timeout=30
	pass udevd.start "systemd-udevd $(udevadm --version) running, rules: $(ls /etc/udev/rules.d | tr '\n' ' ')"
}
# runs_of PATTERN: how many times udevd logged that it ran a RUN+= command matching PATTERN
runs_of() { grep -c "Running command \"$1" /tmp/udevd.log; }
# mkgadget NAME FUNC...: configfs composite gadget with the given functions: acm ncm ecm mass (UDC is bound by the caller)
mkgadget() {
	local g=/sys/kernel/config/usb_gadget/$1; shift
	mkdir "$g" && cd "$g" || return 1
	echo 0x1d6b > idVendor; echo 0x0104 > idProduct; echo 0x0100 > bcdDevice; echo 0x0200 > bcdUSB
	echo 0xEF > bDeviceClass; echo 0x02 > bDeviceSubClass; echo 0x01 > bDeviceProtocol
	mkdir -p strings/0x409 configs/c.1/strings/0x409
	echo virt0001 > strings/0x409/serialnumber; echo SBC-GS-virt > strings/0x409/manufacturer; echo virt-fc > strings/0x409/product
	echo "virt: $*" > configs/c.1/strings/0x409/configuration; echo 250 > configs/c.1/MaxPower
	local f
	for f in "$@"; do
		case "$f" in
			acm) mkdir functions/acm.gs0; ln -s functions/acm.gs0 configs/c.1/ ;;
			ncm) mkdir functions/ncm.usb0; echo 'gsnic%d' > functions/ncm.usb0/ifname; echo 48:6f:73:74:50:43 > functions/ncm.usb0/host_addr; echo 42:61:64:55:53:42 > functions/ncm.usb0/dev_addr; ln -s functions/ncm.usb0 configs/c.1/ ;;
			ecm) mkdir functions/ecm.usb1; echo 'gsnic%d' > functions/ecm.usb1/ifname; echo 48:6f:73:74:50:44 > functions/ecm.usb1/host_addr; echo 42:61:64:55:53:43 > functions/ecm.usb1/dev_addr; ln -s functions/ecm.usb1 configs/c.1/ ;;
			mass) mkdir functions/mass_storage.usb0; echo 0 > functions/mass_storage.usb0/lun.0/ro; echo 1 > functions/mass_storage.usb0/lun.0/removable; echo /virt/disk.img > functions/mass_storage.usb0/lun.0/file; ln -s functions/mass_storage.usb0 configs/c.1/ ;;
		esac
	done
	cd / || return 1
}
# rmgadget NAME: unbind + remove (same order as gs/otg-gadget.sh teardown)
rmgadget() {
	local g=/sys/kernel/config/usb_gadget/$1
	( cd "$g" || exit 1; echo '' > UDC; set -e; find . -type l -exec rm {} \; ; find configs -name strings -exec rmdir {}/0x409 \; ; rmdir configs/*; rmdir strings/*; rmdir functions/*; cd ..; rmdir "$1" ) 2>/tmp/rmg.err
}
runs_ge() { [ "$(runs_of "$1")" -ge "$2" ]; }
noarg_runs_gt() { [ "$(grep -c 'command "/gs/wfb.sh"$' /tmp/udevd.log)" -gt "$1" ]; }
