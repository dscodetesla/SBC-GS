#!/bin/busybox sh
# shellcheck shell=dash
# PID 1 of the QEMU raspi3b guest (see run.sh --load). Markers go to /dev/kmsg so that they interleave with the kernel's
# own messages (the only console QEMU raspi3b gives us is the early PL011 console that prints the kernel log).
# No USB device is present: this only proves that the real Pi kernel ACCEPTS the module (vermagic, modversions CRCs,
# symbol resolution) and that its init path runs up to usb_register(). It proves nothing about probe/RF.
export PATH=/bin
mount -t proc proc /proc; mount -t sysfs sys /sys; mount -t devtmpfs dev /dev
say() { echo "SIM-DKMS-$*" >/dev/kmsg; }
say "BOOT uname=$(uname -m) kernel=$(uname -r)"
# kernel-shipped dependencies first (/mods/order written by run.sh from modinfo depends, files in /mods/deps)
while read -r d; do
	[ -n "$d" ] || continue
	if insmod "/mods/deps/$d.ko"; then say "DEP $d ok"; else say "DEP $d FAIL"; fi
done </mods/order
for f in /mods/*.ko; do
	n="${f##*/}"; n="${n%.ko}"
	[ "$n" = WRONGKERNEL ] && continue
	insmod "$f"; rc=$?
	say "INSMOD $n rc=$rc"
	if [ "$rc" = 0 ]; then
		say "LSMOD $(lsmod | sed -n '2p' | cut -d' ' -f1,2)"
		rmmod "$(lsmod | sed -n '2p' | cut -d' ' -f1)"; say "RMMOD $n rc=$?"
	fi
done
if [ -f /mods/WRONGKERNEL.ko ]; then
	if insmod /mods/WRONGKERNEL.ko 2>/tmp/neg.err; then say "NEG-FAIL module built for another release was ACCEPTED"; else say "NEG-OK refused: $(cat /tmp/neg.err)"; fi
else say "NEG-FAIL no negative-control module"; fi
say "DONE"
poweroff -f
