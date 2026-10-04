#!/bin/bash
# Guest test: virtual USB. dummy_hcd is NOT built in the Ubuntu generic kernel, so the gadget (configfs, UDC usbip-vudc.0) is attached to the
# host controller vhci_hcd of the SAME kernel through a loopback USBIP socket pair (usbip_link). Real: usbcore enumeration, cdc_acm/cdc_ncm/
# cdc_ether/usb-storage/sd drivers, uevents, the project's udev rules in a real systemd-udevd, real gs/button.sh mount_extdisk, real mavp2p.
. /virt/lib.sh
D=/sys/devices/platform/usbip-vudc.0

for m in usbip-vudc vhci-hcd libcomposite usb_f_acm usb_f_ncm usb_f_ecm usb_f_mass_storage cdc-acm cdc_ncm cdc_ether usb-storage; do mod_load "$m" || { summary usb; exit 1; }; done
info "modules: $(grep -c . /proc/modules) loaded; UDC: $(ls /sys/class/udc)"

# project environment for the real scripts
setup_etc
touch /etc/systemd/system/multi-user.target.wants/gs.service
mkdir -p /Videos
cat >> /etc/gs.conf <<'CONF'
gs_enable='yes'
rec_dir='/Videos'
CONF
start_udevd || { summary usb; exit 1; }

nics() { local i; for i in /sys/class/net/*; do [ "${i##*/}" = lo ] || printf '%s ' "${i##*/}"; done; }
plug() { usbip_link plug >/tmp/plug.out 2>&1; }
unplug() { usbip_link unplug >/tmp/unplug.out 2>&1; }
mounted_marker() { [ "$(cat /Videos/MARKER.TXT 2>/dev/null)" = virt-extdisk-marker ]; }

# ======================= phase A: ethernet + mass storage gadget (full unplug/replug cycle) =======================
mkgadget g1 ncm mass || fail usb.gadget.create "configfs gadget g1"
echo "$(ls /sys/class/udc)" > /sys/kernel/config/usb_gadget/g1/UDC && pass usb.gadget.bind "g1 bound to $(cat /sys/kernel/config/usb_gadget/g1/UDC)" || fail usb.gadget.bind "UDC write failed"
eq usb.vudc.status.available 1 "$(cat $D/usbip_status)"
plug; check usb.plug "$(cat /tmp/plug.out)" grep -q plugged /tmp/plug.out
check usb.enumerated.sda1 "no /dev/sda1" wait_for 20 test -b /dev/sda1
check usb.udev.mount_extdisk.fired "rule 99-GS.rules did not run button.sh mount_extdisk" wait_for 20 runs_ge '/gs/button.sh mount_extdisk /dev/sda1' 1
check usb.mount_extdisk.mounted "marker file not visible under /Videos (msg: $(cat /run/pixelpilot.msg 2>/dev/null))" wait_for 20 mounted_marker
info "pixelpilot.msg after mount: $(cat /run/pixelpilot.msg 2>/dev/null)"
info "udev run line: $(grep -m1 'Running command "/gs/button.sh' /tmp/udevd.log | cut -c1-160)"
# host NIC (cdc_ncm) and gadget NIC names after the project's 98-rename.rules
wait_for 10 test -d /sys/class/net/usb0; sleep 1
info "NICs after udev: $(nics)"
# finding: the rule ID_USB_DRIVER=="cdc_ncm" -> usb0 is dead (ID_USB_DRIVER is not set on the net device), the generic eth* + ID_BUS=usb rule wins -> eth1
info "udev props of the host NIC: $(udevadm info -q property /sys/class/net/eth1 2>/dev/null | grep -E 'ID_BUS|ID_USB_DRIVER|ID_NET_DRIVER|ID_NET_NAME' | tr '\n' ' ')"
check usb.rename.host-ncm-to-eth1 "NICs: $(nics)" test -d /sys/class/net/eth1
eq usb.host-nic.driver cdc_ncm "$(basename "$(readlink /sys/class/net/eth1/device/driver 2>/dev/null)")"
if [ -d /sys/class/net/usb0 ]; then fail usb.rename.ncm-to-usb0.rule "usb0 appeared: rule works here"; else pass usb.rename.ncm-to-usb0.rule "98-rename.rules cdc_ncm -> usb0 rule did NOT fire (host NIC is eth1)"; fi
# gadget-side NIC: rule says KERNELS=="gadget"; on this kernel the parent device is named gadget.0
GNIC="$(ls /sys/devices/platform/usbip-vudc.0/gadget.0/net 2>/dev/null | head -1)"
info "gadget NIC name now: '$GNIC' parent: $(basename "$(dirname "$(dirname "$(readlink -f /sys/class/net/"$GNIC")")")")"
eq usb.kernel.gadget-parent-name gadget.0 "$(basename "$(dirname "$(dirname "$(readlink -f /sys/class/net/"$GNIC")")")")"
if [ -d /sys/class/net/radxa0 ]; then fail usb.rename.gadget-to-radxa0.exact-rule "unexpected: KERNELS==\"gadget\" matched"; else pass usb.rename.gadget-to-radxa0.exact-rule "98-rename.rules KERNELS==\"gadget\" does NOT match parent gadget.0 on this kernel: gadget NIC stays '$GNIC'"; fi

# same rule with the parent glob fixed (a COPY of the rules, the real file is not edited): shows the NAME= logic works on a real gadget NIC
rules_swap() { rm -f /etc/udev/rules.d/98-rename.rules; cp "$1" /etc/udev/rules.d/98-rename.rules; udevadm control --reload; }
retrigger_net() { udevadm trigger --action=add --subsystem-match=net; udevadm settle --timeout=20; }
sed 's/KERNELS=="gadget"/KERNELS=="gadget.*"/' /gs/98-rename.rules > /tmp/98-radxa-fixed.rules
sed 's/KERNELS=="gadget"/KERNELS=="gadget.*"/' /virt/rules-rpi4/98-rename.rules > /tmp/98-rpi4-fixed.rules
cp /gs/98-rename.rules /tmp/98-orig.rules
rules_swap /tmp/98-radxa-fixed.rules; retrigger_net; sleep 1
check usb.rename.gadget-to-radxa0.fixed-glob "NICs: $(nics)" test -d /sys/class/net/radxa0
rules_swap /tmp/98-rpi4-fixed.rules; ip link set radxa0 name gsnic9 2>/dev/null; retrigger_net; sleep 1
check usb.rename.gadget-to-rpi0.rpi4-profile "NICs: $(nics)" test -d /sys/class/net/rpi0
rules_swap /tmp/98-orig.rules

# unplug (usbip detach, real usbcore disconnect) and replug
umount -l /Videos 2>/dev/null
unplug; check usb.unplug.sda1-gone "/dev/sda1 still there" wait_for 15 test ! -e /dev/sda1
check usb.unplug.hostnic-gone "eth1 still there: $(nics)" wait_for 10 test ! -d /sys/class/net/eth1
eq usb.unplug.vudc-available 1 "$(cat $D/usbip_status)"
plug; check usb.replug.sda1-back "no /dev/sda1 after replug" wait_for 20 test -b /dev/sda1
check usb.replug.mount_extdisk.fired-twice "mount_extdisk runs: $(runs_of '/gs/button.sh mount_extdisk /dev/sda1')" wait_for 20 runs_ge '/gs/button.sh mount_extdisk /dev/sda1' 2
check usb.replug.remounted "marker not visible after replug" wait_for 20 mounted_marker
check usb.replug.hostnic-back "NICs: $(nics)" wait_for 10 test -d /sys/class/net/eth1
umount -l /Videos 2>/dev/null; unplug; wait_for 15 test ! -e /dev/sda1

# ecm instead of ncm: host NIC is a cdc_ether "eth*" on USB bus -> rule renames it eth1
rmgadget g1 && pass usb.gadget.teardown "g1 removed (configfs, no oops without the serial function)" || fail usb.gadget.teardown "rmgadget failed: $(cat /tmp/rmg.err)"
mkgadget g1 ecm mass; echo "$(ls /sys/class/udc)" > /sys/kernel/config/usb_gadget/g1/UDC; plug
check usb.rename.eth-usb-to-eth1 "NICs: $(nics)" wait_for 20 test -d /sys/class/net/eth1
eq usb.host-nic.eth1.driver cdc_ether "$(basename "$(readlink /sys/class/net/eth1/device/driver 2>/dev/null)")"
umount -l /Videos 2>/dev/null; unplug; wait_for 15 test ! -e /dev/sda1
rmgadget g1 || fail usb.gadget.teardown2 "rmgadget failed: $(cat /tmp/rmg.err)"

# ======================= phase B: serial gadget (FC stand-in): real USB data path ttyGS0 -> ttyACM0 -> mavp2p =======================
mkgadget g2 acm ncm mass; echo "$(ls /sys/class/udc)" > /sys/kernel/config/usb_gadget/g2/UDC; plug
check usb.enumerated.ttyACM0 "no /dev/ttyACM0 (dmesg: $(dmesg | tail -3 | tr '\n' '|'))" wait_for 20 test -c /dev/ttyACM0
check usb.gadget-side.ttyGS0 "no /dev/ttyGS0" test -c /dev/ttyGS0
info "ttyACM0 -> $(readlink -f /sys/class/tty/ttyACM0/device | sed 's|/sys/devices/||')"
udevadm settle --timeout=10
check usb.udev.ttyACM0-event "udevd never saw ttyACM0" grep -q 'ttyACM0' /tmp/udevd.log

if [ -x /usr/bin/mavp2p ]; then
	printf "ROUTER='mavp2p'\nSERIAL_DEV='/dev/ttyACM0'\nSERIAL_BAUD='115200'\nUPSTREAM_PORT='15560'\nGCS_UDP_PORTS='15561'\nHB_SYSID='125'\n" > /tmp/gs-mavlink.conf
	# supervisor stand-in for gs-mavlink.service (Restart=on-failure, RestartSec=2 -> 1 s here; NO systemd in this guest)
	supervise() { local n=0; while :; do SBC_GS_CONFIG_DIR=/opt/sbc/config GS_MAVLINK_CONF=/tmp/gs-mavlink.conf /gs/mavlink/gs-mavlink.sh >>/tmp/gm.log 2>&1; local rc=$?; echo "exit rc=$rc" >> /tmp/sup.log; [ "$rc" = 0 ] && break; n=$((n + 1)); sleep 1; done; }
	: > /tmp/sup.log; : > /tmp/gm.log
	supervise & SUP=$!
	sleep 2
	mavhb gen /dev/ttyGS0 1 100 12 & GEN=$!
	mavhb rx 15561 1 6 > /tmp/rx1.out; HB1="$(grep -c '^HB' /tmp/rx1.out)"
	if [ "$HB1" -ge 10 ]; then pass usb.fc.usb-serial.heartbeats "$HB1 FC heartbeats (sysid 1) in 6 s via ttyGS0 -> USB bulk -> ttyACM0 -> gs-mavlink.sh/mavp2p -> UDP 15561 (10 Hz => ~60)"; else fail usb.fc.usb-serial.heartbeats "only $HB1 heartbeats; router log: $(tail -3 /tmp/gm.log | tr '\n' '|')"; fi
	kill $SUP $GEN 2>/dev/null; pkill mavp2p; pkill mavhb; wait $SUP $GEN 2>/dev/null
	# ---- emulated FC unplug/replug: pty "serial device" at /dev/ttyFC0 (NOT USB, see docs) ----
	sed -i "s|/dev/ttyACM0|/dev/ttyFC0|" /tmp/gs-mavlink.conf
	: > /tmp/sup.log; : > /tmp/gm.log
	mavhb ptygen /dev/ttyFC0 1 100 60 & PTY=$!
	wait_for 5 test -L /dev/ttyFC0
	supervise & SUP=$!
	sleep 2; mavhb rx 15561 1 3 > /tmp/rx2.out; HB2="$(grep -c '^HB' /tmp/rx2.out)"
	if [ "$HB2" -ge 10 ]; then pass usb.fc.pty.heartbeats-before "$HB2 heartbeats in 3 s"; else fail usb.fc.pty.heartbeats-before "only $HB2; router: $(tail -3 /tmp/gm.log | tr '\n' '|')"; fi
	kill $PTY; wait $PTY 2>/dev/null; rm -f /dev/ttyFC0        # unplug
	mavhb rx 15561 1 2 > /tmp/rx3.out 2>&1 &  RX3=$!; sleep 3
	HB3="$(grep -c '^HB' /tmp/rx3.out)"; wait $RX3 2>/dev/null
	eq usb.fc.pty.unplug.no-heartbeats 0 "$HB3"
	sleep 2
	ALIVE="$(pgrep -c mavp2p)"; RESTARTS="$(grep -c 'exit rc=' /tmp/sup.log)"
	info "after unplug: mavp2p processes=$ALIVE, supervisor-observed exits=$RESTARTS; router log tail: $(tail -4 /tmp/gm.log | tr '\n' '|' | cut -c1-300)"
	mavhb ptygen /dev/ttyFC0 1 100 40 & PTY2=$!                  # replug
	mavhb rx 15561 1 10 > /tmp/rx4.out; HB4="$(grep -c '^HB' /tmp/rx4.out)"
	if [ "$HB4" -ge 10 ]; then pass usb.fc.pty.replug.heartbeats-resume "$HB4 heartbeats in 10 s after the device came back"; else fail usb.fc.pty.replug.heartbeats-resume "only $HB4; exits seen by supervisor=$(grep -c 'exit rc=' /tmp/sup.log); router: $(tail -3 /tmp/gm.log | tr '\n' '|')"; fi
	info "supervisor exits total=$(grep -c 'exit rc=' /tmp/sup.log) ($(sort /tmp/sup.log | uniq -c | tr '\n' ' ')); router log: $(grep -c . /tmp/gm.log) lines"
	kill $SUP $PTY2 2>/dev/null; pkill mavp2p; pkill mavhb; wait $SUP $PTY2 2>/dev/null
else
	echo "VIRT-SKIP usb.fc.mavp2p :: mavp2p not installed (go install github.com/bluenviron/mavp2p@v1.3.3; put it on PATH or in \$SIM_CACHE/bin)"
	check usb.fc.gs-mavlink.print "gs-mavlink --print failed" sh -c 'printf "SERIAL_DEV='"'"'/dev/ttyACM0'"'"'\n" > /tmp/c.conf; SBC_GS_CONFIG_DIR=/opt/sbc/config GS_MAVLINK_CONF=/tmp/c.conf /gs/mavlink/gs-mavlink.sh --print | grep -q "serial:/dev/ttyACM0:115200"'
fi

summary usb
# ======================= last, opt-in (VIRT_OOPS_PROBE=1): documented kernel defect (characterization); may wedge the guest =======================
grep -q 'virt.oops=1' /proc/cmdline || { echo "VIRT-SKIP usb.vudc.serial-detach-oops :: opt-in VIRT_OOPS_PROBE=1 (known: vep_dequeue NULL deref on detach with acm bound, kernel $(uname -r))"; exit 0; }
dmesg -c >/dev/null
timeout 10 usbip_link unplug >/dev/null 2>&1; sleep 3
if dmesg | grep -q 'BUG: kernel NULL pointer dereference'; then
	pass usb.vudc.serial-detach-oops.characterized "kernel $(uname -r): $(dmesg | grep -m1 -o 'RIP: .*vep_dequeue[^ ]*')"
else
	pass usb.vudc.serial-detach-oops.not-reproduced "no oops on kernel $(uname -r): serial gadget unplug may now be testable through vudc"
fi
