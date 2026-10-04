#!/bin/busybox sh
# PID 1 of the QEMU guest (see tests/sim/qemu_hwsim.sh). Prints SIM-* markers on ttyS0 that the host script greps.
export PATH=/bin:/usr/sbin
mount -t proc proc /proc; mount -t sysfs sys /sys; mount -t devtmpfs dev /dev
exec >/dev/ttyS0 2>&1 </dev/ttyS0
echo "SIM-BOOT uname=$(uname -m) kernel=$(uname -r)"
for m in libarc4 cfg80211 mac80211 mac80211_hwsim; do insmod /mods/$m.ko && echo "insmod $m ok" || echo "SIM-INSMOD-FAIL $m"; done
sleep 1
iw dev wlan0 set type monitor && iw dev wlan1 set type monitor && echo "SIM-MONITOR-OK"
ip link set wlan0 up; ip link set wlan1 up; ip link set lo up
iw dev wlan0 set channel 36 HT20; iw dev wlan1 set channel 36 HT20
iw dev wlan0 info | grep -E 'type|channel'
inj wlan0 wlan1 && echo "SIM-INJECT-OK" || echo "SIM-INJECT-FAIL"
# real wfb-ng (unmodified wfb_tx/wfb_rx): drone key on wlan0 -> air (hwsim) -> gs key on wlan1
wfb_rx -p 0 -c 127.0.0.1 -u 15700 -K /etc/gs.key wlan1 >/tmp/rx.log 2>&1 &
RX=$!
sleep 1
wfb_tx -p 0 -u 15702 -K /etc/drone.key wlan0 >/tmp/tx.log 2>&1 &
TX=$!
sleep 1
udpt 15702 15700 100 && echo "SIM-WFB-OK" || echo "SIM-WFB-FAIL"
# negative control: receiver on another channel must hear nothing (hwsim models channel separation)
iw dev wlan1 set channel 149 HT20
sleep 1
if udpt 15702 15700 30 >/tmp/neg.out; then echo "SIM-NEG-FAIL $(cat /tmp/neg.out)"; else echo "SIM-NEG-OK $(cat /tmp/neg.out)"; fi
iw dev wlan1 set channel 36 HT20
# negative control 2: a receiver with an UNRELATED keypair must not accept anything (authentication, not just FEC)
kill $RX 2>/dev/null
wfb_rx -p 0 -c 127.0.0.1 -u 15700 -K /etc/wrong.key wlan1 >/tmp/rx2.log 2>&1 &
RX2=$!
sleep 1
if udpt 15702 15700 30 >/tmp/neg2.out; then echo "SIM-KEY-FAIL $(cat /tmp/neg2.out)"; else echo "SIM-KEY-OK $(cat /tmp/neg2.out)"; fi
kill $TX $RX2 2>/dev/null
echo "SIM-DONE"
poweroff -f
