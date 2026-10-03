#!/bin/busybox sh
# shellcheck shell=dash
# PID 1 of the virtual-devices QEMU guest (tests/sim/virt/run.sh). Real bash/coreutils/util-linux/libgpiod/udev from the host
# distro run on a stock Ubuntu generic kernel. Output on ttyS0: lines "VIRT-PASS|VIRT-FAIL|VIRT-INFO|VIRT-SKIP ..." and SIM-DONE.
export PATH=/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin
mount -t proc proc /proc; mount -t sysfs sys /sys; mount -t devtmpfs dev /dev
mkdir -p /run /tmp /sys/kernel/config; ip link set lo up 2>/dev/null; mount -t tmpfs tmpfs /run; mount -t tmpfs tmpfs /tmp
mount -t configfs none /sys/kernel/config; mkdir -p /dev/pts; mount -t devpts devpts /dev/pts
exec >/dev/ttyS0 2>&1 </dev/ttyS0
mode="$(sed -n 's/.*virt\.mode=\([a-z,]*\).*/\1/p' /proc/cmdline)"
echo "SIM-BOOT uname=$(uname -m) kernel=$(uname -r) mode=$mode"
echo 0 > /proc/sys/kernel/printk 2>/dev/null
for m in $(echo "$mode" | tr ',' ' '); do
	echo "SIM-MODE $m"
	timeout -s KILL 330 /usr/bin/bash /virt/t_"$m".sh || echo "VIRT-FAIL $m :: test script exited rc=$?"
done
echo "SIM-DONE"
poweroff -f
