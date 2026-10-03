#!/bin/busybox sh
# shellcheck shell=dash
# PID 1 of the evdev QEMU guest (tests/sim/evdev/run.sh): real kernel input stack (uinput, evdev built in; hid/hid-generic/uhid loaded),
# real python-evdev + pymavlink + bench/tx12_bridge.py. Output on ttyS0: unittest lines, then EVDEV-RC <rc> and EVDEV-DONE.
export PATH=/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin
mount -t proc proc /proc; mount -t sysfs sys /sys; mount -t devtmpfs dev /dev
mount -t tmpfs tmpfs /run; mount -t tmpfs tmpfs /tmp; chmod 1777 /tmp
exec >/dev/ttyS0 2>&1 </dev/ttyS0
ifconfig lo 127.0.0.1 up 2>/dev/null || ip link set lo up
echo 0 > /proc/sys/kernel/printk 2>/dev/null
echo "EVDEV-BOOT uname=$(uname -m) kernel=$(uname -r)"
for m in $(cat /mods/order); do insmod "/mods/$m" || echo "EVDEV-WARN insmod $m failed"; done
only="$(sed -n 's/.*evdev\.only=\([^ ]*\).*/\1/p' /proc/cmdline)"
echo "EVDEV-DEV uinput=$(ls /dev/uinput 2>&1) uhid=$(ls /dev/uhid 2>&1)"
cd /repo/tests/sim/evdev || exit 1
export PYTHONPATH=/py/site PYTHONDONTWRITEBYTECODE=1 EVDEV_GUEST=1 PY=/usr/local/bin/python HOME=/tmp TMPDIR=/tmp
if [ -n "$only" ]; then timeout -s KILL 800 /usr/local/bin/python test_evdev.py -v -k "$only"; else timeout -s KILL 800 /usr/local/bin/python test_evdev.py -v; fi
echo "EVDEV-RC $?"
echo "EVDEV-DONE"
poweroff -f
