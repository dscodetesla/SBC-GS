#!/usr/bin/env bash
# Real wfb-ng over a SIMULATED Wi-Fi radio pair: QEMU x86_64 guest with a stock Ubuntu kernel + mac80211_hwsim,
# wlan0/wlan1 in monitor mode, unmodified wfb_tx/wfb_rx (no LD_PRELOAD shim), UDP in -> radio -> UDP out.
# Rootless: only apt-get download (no install of kernels), dpkg -x, cpio, qemu (TCG, or KVM if /dev/kvm is usable).
# Needs: qemu-system-x86 busybox-static iw cpio gzip zstd gcc make git + dev libs for wfb-ng
#        (libsodium-dev libpcap-dev libevent-dev g++). ~170 MB download, ~25 s boot+test under TCG.
# Env: SIM_CACHE (default ~/.cache/sbc-gs-sim), WFB_NG_DIR (built tree) / WFB_NG_REF, KERNEL_PKG_VER (default: archive candidate).
# Exit: 0 pass, 1 fail, 77 skipped (a prerequisite is missing, nothing was tested).
# Proves: wfb-ng + monitor-mode injection + radiotap handling + channel separation on a stock mac80211 stack.
# Does NOT prove: any real driver (rtl88xxau/8812eu/brcmfmac), DKMS build on the Pi kernel, RF, USB, power.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="${SIM_CACHE:-$HOME/.cache/sbc-gs-sim}"
WFB_NG_REF="${WFB_NG_REF:-2fe252b2f451c1ccfb16968e064fe1cdb18baaa0}"
skip() { echo "SKIP qemu_hwsim: $*"; exit 77; }
for t in qemu-system-x86_64 cpio gzip zstd gcc make git apt-get dpkg-deb; do command -v "$t" >/dev/null || skip "$t not installed"; done
BB="$(command -v busybox || true)"; [ -n "$BB" ] || skip "busybox-static not installed"
file "$BB" 2>/dev/null | grep -q 'statically linked' || skip "$BB is not static (apt install busybox-static)"
IW="$(command -v iw || true)"; [ -n "$IW" ] || skip "iw not installed"

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
mkdir -p "$CACHE"

# ---- kernel + modules (Ubuntu generic: mac80211_hwsim lives in linux-modules-extra; the Azure runner kernel has none) ----
ver="${KERNEL_PKG_VER:-$(apt-cache depends linux-image-generic 2>/dev/null | sed -n 's/^ *Depends: linux-image-\(.*-generic\)$/\1/p' | head -1)}"
[ -n "$ver" ] || skip "cannot determine the generic kernel version (apt-cache depends linux-image-generic)"
kdir="$CACHE/kernel-$ver"
if [ ! -f "$kdir/boot/vmlinuz-$ver" ] || [ ! -f "$kdir/lib/modules/$ver/kernel/drivers/net/wireless/virtual/mac80211_hwsim.ko.zst" ]; then
	rm -rf "$kdir"; mkdir -p "$kdir" "$work/deb"
	( cd "$work/deb" && apt-get download "linux-image-unsigned-$ver" "linux-modules-$ver" "linux-modules-extra-$ver" >/dev/null 2>&1 ) \
		|| skip "apt-get download of the kernel packages for $ver failed (apt sources / network)"
	for d in "$work"/deb/*.deb; do dpkg-deb -x "$d" "$kdir" || skip "dpkg-deb -x failed"; done
fi
K="$kdir/lib/modules/$ver/kernel"

# ---- wfb-ng ----
src="${WFB_NG_DIR:-$CACHE/wfb-ng-$WFB_NG_REF}"
if [ ! -x "$src/wfb_tx" ] || [ ! -x "$src/wfb_rx" ]; then
	if [ ! -d "$src/src" ]; then
		mkdir -p "$src"
		( cd "$src" && git init -q && git fetch -q --depth 1 https://github.com/svpcom/wfb-ng.git "$WFB_NG_REF" && git checkout -q FETCH_HEAD ) || skip "cannot fetch wfb-ng $WFB_NG_REF"
	fi
	( cd "$src" && make all_bin >"$work/build.log" 2>&1 ) || skip "wfb-ng build failed: $(tail -2 "$work/build.log")"
fi
( cd "$work" && "$src/wfb_keygen" >/dev/null 2>&1 && mkdir other && cd other && "$src/wfb_keygen" >/dev/null 2>&1 ) || skip "wfb_keygen failed"

# ---- initramfs ----
rd="$work/rd"; mkdir -p "$rd"/{bin,lib,lib64,proc,sys,dev,mods,etc,usr/sbin,tmp}
cp "$BB" "$rd/bin/busybox"
for a in sh ls cat mount insmod ip poweroff sleep echo uname grep kill; do ln -sf busybox "$rd/bin/$a"; done
for m in lib/crypto/libarc4 net/wireless/cfg80211 net/mac80211/mac80211 drivers/net/wireless/virtual/mac80211_hwsim; do
	zstd -dc "$K/$m.ko.zst" >"$rd/mods/$(basename "$m").ko" || skip "cannot decompress module $m"
done
gcc -static -O2 -D_GNU_SOURCE -o "$rd/bin/inj" "$HERE/guest/inj.c" && gcc -static -O2 -o "$rd/bin/udpt" "$HERE/guest/udpt.c" || skip "cannot build guest helpers (static glibc: libc6-dev)"
cp "$src/wfb_tx" "$src/wfb_rx" "$rd/bin/"; cp "$IW" "$rd/usr/sbin/iw"
cp "$work/drone.key" "$work/gs.key" "$rd/etc/"; cp "$work/other/gs.key" "$rd/etc/wrong.key"
for b in "$rd/bin/wfb_tx" "$rd/bin/wfb_rx" "$rd/usr/sbin/iw"; do
	ldd "$b" | awk '/=>/{print $3} /ld-linux/{print $1}' | while read -r l; do
		[ -f "$l" ] && { mkdir -p "$rd$(dirname "$l")"; cp -L --update=none "$l" "$rd$l"; }
	done
done
cp "$HERE/guest/init.sh" "$rd/init"; chmod +x "$rd/init"
( cd "$rd" && find . | cpio -o -H newc --quiet 2>/dev/null | gzip -1 >"$work/initrd.gz" ) || skip "cpio failed"

# ---- run ----
accel=(); [ -w /dev/kvm ] && accel=(-enable-kvm -cpu host)
timeout 240 qemu-system-x86_64 "${accel[@]}" -m 1G -smp 2 -kernel "$kdir/boot/vmlinuz-$ver" -initrd "$work/initrd.gz" \
	-append "console=ttyS0 rdinit=/init quiet" -display none -no-reboot -serial "file:$work/serial.log" -monitor none >/dev/null 2>&1
log="$work/serial.log"
fail=0
chk() { if grep -q "$1" "$log"; then echo "PASS  $2 ($(grep -m1 "$1" "$log" | cut -c1-90))"; else echo "FAIL  $2"; fail=1; fi; }
chk 'SIM-BOOT' "guest booted ($ver, ${accel[*]:-TCG})"
chk 'SIM-MONITOR-OK' "mac80211_hwsim: two radios switched to monitor mode"
chk 'SIM-INJECT-OK' "raw 802.11 injection wlan0 -> wlan1 (radiotap RX header present)"
chk 'SIM-WFB-OK' "wfb_tx -> hwsim -> wfb_rx: 100 UDP datagrams delivered (>=90%)"
chk 'SIM-NEG-OK' "negative control: receiver on another channel hears nothing"
chk 'SIM-KEY-OK' "negative control: receiver with an unrelated keypair accepts nothing"
grep -E 'UDPT|INJECT ' "$log" | sed 's/^/      /'
[ "$fail" = 0 ] || { echo "---- guest log tail"; tail -25 "$log"; exit 1; }
echo "qemu_hwsim: ALL PASS"
