#!/usr/bin/env bash
# Virtual kernel devices in a QEMU x86_64 guest: the project's REAL scripts, REAL libgpiod tools and REAL udev run against
# emulated hardware (gpio-sim GPIO, usbip-vudc+vhci USB gadget/host, mac80211_hwsim radios). Rootless (QEMU TCG, or KVM if /dev/kvm is writable).
#   tests/sim/virt/run.sh --check          static asserts on files/scripts/pin map, no VM, no network, <2 s
#   tests/sim/virt/run.sh gpio|usb|radio|roconf   one VM boot with that test set (~40-90 s under TCG; roconf: see below)
#   tests/sim/virt/run.sh all              gpio,usb,radio,roconf in ONE boot
#   roconf = the image layout for gs.conf: rootfs ext4 mounted read-only, /etc = overlayfs, /etc/gs.conf -> /config/gs.conf on a writable ext4 (and FAT) partition;
#            REAL gs/lib/gsconf.sh, gs/gs-applyconf.sh, gs/gsmenu.sh; kill -9 mid-write, read-only /config, unprivileged writer (docs/SIM-VIRT-DEVICES.md section 12)
# Env: SIM_CACHE (default ~/.cache/sbc-gs-sim), KERNEL_PKG_VER (default: cached kernel-*-generic, else apt candidate),
#      VIRT_OOPS_PROBE=1 (usb mode: finish with the deliberate vudc+acm detach that oopses this kernel; may wedge the guest),
#      VIRT_KEEP_LOG=<file> (copy the guest serial log),
#      GS_ROOT (repo whose gs/ is installed in the guest as /gs; default this repo; used for mutation checks on a COPY),
#      DOSFSTOOLS_DEB_VER (roconf: mkfs.fat comes from the host, else from `apt-get download dosfstools` into SIM_CACHE; without it the FAT variant is SKIPped),
#      WFB_NG_DIR / WFB_NG_REF (built wfb-ng tree, same as qemu_hwsim.sh), SMOKE_VIRT=1 is read by tests/sim/smoke.sh (when registered).
# Needs on the host: qemu-system-x86_64 busybox(static) cpio gzip zstd gcc kmod(depmod, modprobe) iw ip udevadm systemd-udevd, mke2fs, python3;
#      network (apt-get download, no root) only for the first run: kernel packages (~170 MB) + gpiod 1.6.x (~70 kB); radio mode also needs wfb-ng
#      (git + make + libsodium/libpcap/libevent dev); usb mode uses mavp2p from PATH or $SIM_CACHE/bin (else the FC-serial part is SKIPped).
# Exit: 0 pass, 1 fail, 77 skipped (a prerequisite is missing, nothing was tested).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
ROOT="${GS_ROOT:-$REPO}"
CACHE="${SIM_CACHE:-$HOME/.cache/sbc-gs-sim}"
WFB_NG_REF="${WFB_NG_REF:-2fe252b2f451c1ccfb16968e064fe1cdb18baaa0}"
GUEST="$HERE/guest"

skip() { echo "SKIP virt: $*"; exit 77; }
usage() { sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 2; }

# ------------------------------------------------------------------ --check (no VM)
check_static() {
	local fail=0 f n
	ok() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2${3:+ ($3)}"; fail=1; fi; }
	for f in run.sh guest/init.sh guest/lib.sh guest/t_gpio.sh guest/t_usb.sh guest/t_radio.sh guest/t_roconf.sh guest/hwsimctl.c guest/usbip_link.c guest/mavhb.c; do
		[ -f "$HERE/$f" ]; ok $? "file $f exists"
	done
	for f in run.sh guest/init.sh guest/lib.sh guest/t_gpio.sh guest/t_usb.sh guest/t_radio.sh guest/t_roconf.sh; do
		bash -n "$HERE/$f" 2>/dev/null; ok $? "bash -n $f"
	done
	if command -v shellcheck >/dev/null; then
		( cd "$HERE" && shellcheck -x -S warning run.sh guest/lib.sh guest/t_gpio.sh guest/t_usb.sh guest/t_radio.sh guest/t_roconf.sh guest/init.sh ) >/tmp/virt-sc.$$ 2>&1
		ok $? "shellcheck -x -S warning" "$(head -3 /tmp/virt-sc.$$ | tr '\n' ' ')"; rm -f /tmp/virt-sc.$$
	else echo "SKIP  shellcheck not installed"; fi
	if command -v gcc >/dev/null; then
		for f in hwsimctl usbip_link mavhb; do gcc -fsyntax-only -Wall -Wextra "$GUEST/$f.c" 2>/dev/null; ok $? "gcc -Wall -Wextra $f.c"; done
	else echo "SKIP  gcc not installed"; fi
	# the board contracts the VM test models: pin map entries used by the default gs.conf buttons/LEDs and by the tests
	local map="$ROOT/gs/boards/rpi4/pinmap.conf"
	for pair in 32:12 38:20 40:21 15:22 12:18 16:23 18:24 13:27 11:17 22:25; do
		n="$(awk -v p="${pair%%:*}" '$1 !~ /^#/ && $1 == p {print $2}' "$map")"
		[ "$n" = "${pair##*:}" ]; ok $? "pinmap rpi4: physical ${pair%%:*} -> BCM ${pair##*:}" "got '$n'"
	done
	grep -q "^GPIO_PIN_PREFIX='PIN_'" "$ROOT/gs/boards/radxa-zero3/board.conf"; ok $? "radxa-zero3 GPIO_PIN_PREFIX is PIN_"
	grep -q "^GPIO_PIN_PREFIX='GPIO'" "$ROOT/gs/boards/rpi4/board.conf"; ok $? "rpi4 GPIO_PIN_PREFIX is GPIO"
	# the code paths the VM tests drive must still look as the tests expect
	grep -q 'gpiomon -r -s -n 1 -B pull-down' "$ROOT/gs/button.sh"; ok $? "button.sh: rising-edge wait"
	grep -q 'gpiomon -f -s -n 1 -B pull-down' "$ROOT/gs/button.sh"; ok $? "button.sh: falling-edge wait"
	grep -q '\-lt 200' "$ROOT/gs/button.sh"; ok $? "button.sh: short/long threshold 200 (centiseconds)"
	grep -q 'KERNELS=="gadget", NAME="radxa0"' "$ROOT/gs/98-rename.rules"; ok $? "98-rename.rules: gadget NIC rule"
	grep -q 'KERNEL=="sda1", RUN+="/gs/button.sh mount_extdisk \$devnode"' "$ROOT/gs/99-GS.rules"; ok $? "99-GS.rules: sda1 mount_extdisk rule"
	grep -q 'ACTION=="add", ENV{ID_NET_NAME}!="wifi0", RUN+="/gs/wfb.sh \$name"' "$ROOT/gs/99-GS.rules" \
		|| grep -q 'KERNEL=="wl\*", ACTION=="add", ENV{ID_NET_NAME}!="wifi0", RUN+="/gs/wfb.sh \$name"' "$ROOT/gs/99-GS.rules"; ok $? "99-GS.rules: wl* add rule"
	grep -q 'KERNEL=="wl\*", ACTION=="remove", RUN+="/gs/wfb.sh"' "$ROOT/gs/99-GS.rules"; ok $? "99-GS.rules: wl* remove rule"
	grep -q 'invocation:gs.service' "$ROOT/gs/wfb.sh"; ok $? "wfb.sh: gs.service gate present"
	for fn in toggle_record cleanup_record_files change_otg_mode mount_extdisk button_action; do
		grep -q "^function $fn()" "$ROOT/gs/button.sh"; ok $? "button.sh defines $fn"
	done
	"$ROOT/gs/boards/render-udev.sh" rpi4 /tmp/virt-render.$$ >/dev/null 2>&1 && grep -q 'NAME="rpi0"' /tmp/virt-render.$$/98-rename.rules; ok $? "render-udev.sh rpi4 yields gadget name rpi0"
	rm -rf /tmp/virt-render.$$
	for m in gpio usb radio roconf; do grep -q "^VIRT-MODE-$m\b\|t_$m" "$HERE/run.sh"; ok $? "run.sh knows mode $m"; done
	# roconf: the library, the scripts and the layout the test models must still look as the guest test expects
	[ -f "$ROOT/gs/lib/gsconf.sh" ]; ok $? "gs/lib/gsconf.sh exists (roconf mode)"
	for fn in gsconf_set_many gsconf_set_quoted gsconf_check gsconf_can_write gsconf_norm_value; do
		grep -q "^$fn()" "$ROOT/gs/lib/gsconf.sh"; ok $? "gsconf.sh defines $fn"
	done
	grep -q 'source /gs/lib/gsconf.sh' "$ROOT/gs/gs-applyconf.sh"; ok $? "gs-applyconf.sh sources lib/gsconf.sh"
	grep -q 'source /gs/lib/gsconf.sh' "$ROOT/gs/gsmenu.sh"; ok $? "gsmenu.sh sources lib/gsconf.sh"
	[ "$(grep -cE 'sed -i.*/etc/gs\.conf' "$ROOT/gs/gs-applyconf.sh" "$ROOT/gs/gsmenu.sh" | awk -F: '{s+=$2} END {print s+0}')" = 0 ]; ok $? "no direct sed -i on /etc/gs.conf left in gs-applyconf.sh/gsmenu.sh"
	grep -q '^cp -r lib boards' "$ROOT/gs/install.sh"; ok $? "install.sh copies the whole gs/lib (gsconf.sh included)"
	grep -q 'roconf' "$HERE/guest/init.sh" || grep -q 't_"$m"' "$HERE/guest/init.sh"; ok $? "guest init dispatches t_<mode>.sh"
	[ "$fail" = 0 ] && echo "virt --check: ALL PASS" || echo "virt --check: FAILED"
	return "$fail"
}

modes=""
case "${1:-}" in
	--check) check_static; exit $? ;;
	gpio|usb|radio|roconf) modes="$1" ;;
	all) modes="gpio,usb,radio,roconf" ;;
	*) usage ;;
esac
[ -z "${2:-}" ] || usage

# ------------------------------------------------------------------ prerequisites
for t in qemu-system-x86_64 cpio gzip zstd gcc depmod modprobe apt-get dpkg-deb mke2fs python3 udevadm; do command -v "$t" >/dev/null || skip "$t not installed"; done
BB="$(command -v busybox || true)"; [ -n "$BB" ] || skip "busybox-static not installed"
file "$BB" 2>/dev/null | grep -q 'statically linked' || skip "$BB is not static (apt install busybox-static)"
UDEVD=/usr/lib/systemd/systemd-udevd; [ -x "$UDEVD" ] || UDEVD=/lib/systemd/systemd-udevd; [ -x "$UDEVD" ] || skip "systemd-udevd not installed"
work="$(mktemp -d)"; trap '[ -z "${VIRT_KEEP_LOG:-}" ] || cp "$work/serial.log" "$VIRT_KEEP_LOG" 2>/dev/null; rm -rf "$work"' EXIT
mkdir -p "$CACHE"

# kernel: Ubuntu generic with gpio-sim, usbip-vudc, vhci-hcd, libcomposite, mac80211_hwsim as modules (see docs/SIM-VIRT-DEVICES.md)
ver="${KERNEL_PKG_VER:-}"
if [ -z "$ver" ]; then
	ver="$(find "$CACHE" -maxdepth 1 -name 'kernel-*-generic' -printf '%f\n' 2>/dev/null | sed 's/^kernel-//' | sort -V | tail -1)"
	[ -n "$ver" ] || ver="$(apt-cache depends linux-image-generic 2>/dev/null | sed -n 's/^ *Depends: linux-image-\(.*-generic\)$/\1/p' | head -1)"
fi
[ -n "$ver" ] || skip "cannot determine the generic kernel version"
kdir="$CACHE/kernel-$ver"
if [ ! -f "$kdir/boot/vmlinuz-$ver" ] || [ ! -f "$kdir/lib/modules/$ver/kernel/drivers/gpio/gpio-sim.ko.zst" ]; then
	rm -rf "$kdir"; mkdir -p "$kdir" "$work/deb"
	( cd "$work/deb" && apt-get download "linux-image-unsigned-$ver" "linux-modules-$ver" "linux-modules-extra-$ver" >/dev/null 2>&1 ) \
		|| skip "apt-get download of the kernel packages for $ver failed (apt sources / network)"
	for d in "$work"/deb/*.deb; do dpkg-deb -x "$d" "$kdir" || skip "dpkg-deb -x failed"; done
fi
M="$kdir/lib/modules/$ver"
[ -f "$M/modules.dep" ] || depmod -b "$kdir" "$ver" 2>/dev/null || skip "depmod failed"

# libgpiod v1 tools (Ubuntu 24.04 gpiod 1.6.3; the repo targets libgpiod v1, docs/PI-PORT.md)
gdir="$(find "$CACHE" -maxdepth 1 -name 'gpiod-1.*' | sort -V | tail -1)"
if [ -z "$gdir" ] || [ ! -x "$gdir/usr/bin/gpiomon" ]; then
	mkdir -p "$work/gdeb"
	( cd "$work/gdeb" && apt-get download gpiod libgpiod2t64 >/dev/null 2>&1 ) || skip "apt-get download gpiod libgpiod2t64 failed"
	gv="$(dpkg-deb -f "$work"/gdeb/gpiod_*.deb Version | sed 's/-.*//')"; gdir="$CACHE/gpiod-$gv"; mkdir -p "$gdir"
	for d in "$work"/gdeb/*.deb; do dpkg-deb -x "$d" "$gdir"; done
fi

# wfb-ng (radio mode only)
src=""
case "$modes" in *radio*|all)
	src="${WFB_NG_DIR:-$CACHE/wfb-ng-$WFB_NG_REF}"
	if [ ! -x "$src/wfb_tx" ] || [ ! -x "$src/wfb_rx" ]; then
		for t in git make g++; do command -v "$t" >/dev/null || skip "$t not installed (wfb-ng build)"; done
		if [ ! -d "$src/src" ]; then
			mkdir -p "$src"
			( cd "$src" && git init -q && git fetch -q --depth 1 https://github.com/svpcom/wfb-ng.git "$WFB_NG_REF" && git checkout -q FETCH_HEAD ) || skip "cannot fetch wfb-ng $WFB_NG_REF"
		fi
		( cd "$src" && make all_bin >"$work/build.log" 2>&1 ) || skip "wfb-ng build failed: $(tail -2 "$work/build.log")"
	fi ;;
esac
MAVP2P="$(command -v mavp2p || true)"; [ -n "$MAVP2P" ] || { [ -x "$CACHE/bin/mavp2p" ] && MAVP2P="$CACHE/bin/mavp2p"; }

# ------------------------------------------------------------------ initramfs
rd="$work/rd"
mkdir -p "$rd"/usr/{bin,sbin,lib,lib64} "$rd"/{proc,sys,dev,run,tmp,etc,mods,virt,gs-etc,var,Videos}
ln -s usr/bin "$rd/bin"; ln -s usr/sbin "$rd/sbin"; ln -s usr/lib "$rd/lib"; ln -s usr/lib64 "$rd/lib64"
cp "$BB" "$rd/usr/bin/busybox"
copy_bin() { # copy a host binary (path or name) and its shared libraries, keeping host paths (merged-usr layout)
	local b l p; b="$(type -P "$1" || echo "$1")"; [ -e "$b" ] || { skip "host binary $1 not found"; }
	b="$(readlink -f "$b")"
	cp -L "$b" "$rd/usr/bin/$(basename "$1")" 2>/dev/null || cp -L "$b" "$rd/usr/bin/$(basename "$b")"
	ldd "$b" 2>/dev/null | awk '/=>/{print $3} /ld-linux/{print $1}' | while read -r l; do
		[ -f "$l" ] || continue
		p="$(readlink -f "$l")"; mkdir -p "$rd$(dirname "$l")"; [ -e "$rd$l" ] || cp -L "$p" "$rd$l"
	done
}
for b in bash cat cut tr df grep sed awk findmnt nsenter mount umount sleep date mkfifo ls mkdir ln chmod timeout env dirname readlink \
	basename touch rm find cp head tail wc sort seq tee ip iw udevadm mktemp id uname stty dd od cmp setsid tail kill pkill pgrep ps mv sync \
	stat chown flock setpriv md5sum comm diff; do
	[ -n "$(type -P "$b")" ] && copy_bin "$b"
done
copy_bin "$UDEVD"; mkdir -p "$rd/usr/lib/systemd"; mv "$rd/usr/bin/systemd-udevd" "$rd/usr/lib/systemd/systemd-udevd"
for b in gpiodetect gpiofind gpioget gpioinfo gpiomon gpioset; do cp "$gdir/usr/bin/$b" "$rd/usr/bin/$b"; done
ldd "$gdir/usr/bin/gpiomon" >/dev/null 2>&1
mkdir -p "$rd/usr/lib/x86_64-linux-gnu"; cp -L "$gdir"/usr/lib/x86_64-linux-gnu/libgpiod.so.* "$rd/usr/lib/x86_64-linux-gnu/"
for l in /lib/x86_64-linux-gnu/libnss_files.so.2 /lib/x86_64-linux-gnu/libresolv.so.2; do [ -f "$l" ] && cp -L "$l" "$rd$l" 2>/dev/null; done
for a in sh ls cat mount insmod poweroff sleep echo uname grep kill sed tr timeout dmesg ip rmdir xargs pkill uniq; do [ -e "$rd/usr/bin/$a" ] || ln -s busybox "$rd/usr/bin/$a"; done
ln -sf busybox "$rd/usr/bin/insmod"; ln -sf busybox "$rd/usr/bin/poweroff"; ln -sf busybox "$rd/usr/bin/rmmod"; ln -sf busybox "$rd/usr/bin/lsmod"
# helpers (static)
for h in hwsimctl usbip_link mavhb; do gcc -static -O2 -D_GNU_SOURCE -o "$rd/usr/bin/$h" "$GUEST/$h.c" || skip "cannot build $h (static glibc: libc6-dev)"; done
# wfb-ng binaries for /usr/bin (wfb.sh's aggregator path calls /usr/bin/wfb_rx)
if [ -n "$src" ]; then ( cd "$work" && "$src/wfb_keygen" >/dev/null 2>&1 ) && cp "$work/gs.key" "$work/drone.key" "$rd/etc/" || skip "wfb_keygen failed"; cp "$src/wfb_tx" "$src/wfb_rx" "$rd/usr/bin/"; for b in wfb_tx wfb_rx; do ldd "$rd/usr/bin/$b" | awk '/=>/{print $3}' | while read -r l; do [ -f "$l" ] && { mkdir -p "$rd$(dirname "$l")"; cp -L --update=none "$l" "$rd$l"; }; done; done; fi
[ -z "$MAVP2P" ] || cp "$MAVP2P" "$rd/usr/bin/mavp2p"
# kernel modules (decompressed; dependency order precomputed from modules.dep)
want="gpio-sim usbip-vudc vhci-hcd libcomposite usb_f_acm usb_f_ncm usb_f_ecm usb_f_mass_storage cdc-acm cdc_ncm cdc_ether usb-storage mac80211_hwsim"
case "$modes" in *roconf*) want="$want overlay nls_iso8859-1" ;; esac   # overlayfs and nls_iso8859-1 (default iocharset of vfat on this kernel) are modules in the Ubuntu generic kernel (ext4, vfat, virtio-blk are built in)
for m in $want; do
	: >"$rd/mods/dep.$m"
	modprobe -d "$kdir" -S "$ver" --show-depends "$m" 2>/dev/null | awk '$1=="insmod"{print $2}' | while read -r p; do
		bn="$(basename "$p" .zst)"
		[ -f "$rd/mods/$bn" ] || zstd -dqc "$p" >"$rd/mods/$bn" || exit 1
		echo "$bn" >>"$rd/mods/dep.$m"
	done
	[ -s "$rd/mods/dep.$m" ] || { rm -f "$rd/mods/dep.$m"; echo "NOTE: module $m not provided by kernel $ver (builtin or missing)" >&2; }
done
# /gs (the project's scripts, from $ROOT), board profiles, udev: stock systemd rules + the project's two rule files, passwd/group
mkdir -p "$rd/gs"; cp -a "$ROOT/gs/." "$rd/gs/"
# layered config loader used by gs-mavlink.sh (must NOT live under /config: the script refuses to run code from there)
[ ! -d "$ROOT/config" ] || { mkdir -p "$rd/opt/sbc"; cp -a "$ROOT/config" "$rd/opt/sbc/config"; }
mkdir -p "$rd/usr/lib/udev/rules.d" "$rd/etc/udev/rules.d" "$rd/usr/lib/systemd/network" "$rd/etc/systemd/system/multi-user.target.wants"
for r in 50-udev-default 73-special-net-names 75-net-description 80-net-setup-link; do cp /usr/lib/udev/rules.d/$r.rules "$rd/usr/lib/udev/rules.d/"; done
cp /usr/lib/systemd/network/73-usb-net-by-mac.link /usr/lib/systemd/network/99-default.link "$rd/usr/lib/systemd/network/"
cp "$ROOT/gs/98-rename.rules" "$ROOT/gs/99-GS.rules" "$rd/etc/udev/rules.d/"
mkdir -p "$rd/virt/rules-rpi4"; "$ROOT/gs/boards/render-udev.sh" rpi4 "$rd/virt/rules-rpi4" >/dev/null
cp /etc/passwd /etc/group "$rd/etc/"
cp "$ROOT/gs/gs.conf" "$rd/gs-etc/gs.conf"
cp "$GUEST/lib.sh" "$GUEST"/t_*.sh "$rd/virt/"
cp "$GUEST/init.sh" "$rd/init"; chmod +x "$rd/init" "$rd"/virt/*.sh
# external-disk image for the USB mass-storage test: MBR + one ext4 partition (sda1) carrying a marker file
python3 - "$work/disk.img" <<'PY'
import struct, sys
size_mb, start = 16, 2048
with open(sys.argv[1], "wb") as f:
    f.truncate(size_mb * 1024 * 1024)
    part = struct.pack("<B3sB3sII", 0, b"\x00\x00\x00", 0x83, b"\x00\x00\x00", start, size_mb * 2048 - start)
    f.seek(446); f.write(part); f.seek(510); f.write(b"\x55\xaa")
PY
mkdir -p "$work/disk-src"; echo "virt-extdisk-marker" >"$work/disk-src/MARKER.TXT"
mke2fs -q -F -t ext4 -d "$work/disk-src" -E offset=$((2048 * 512)) "$work/disk.img" $((16 * 1024 - 1024)) >/dev/null 2>&1 || skip "mke2fs for the disk image failed"
mkdir -p "$rd/virt"; cp "$work/disk.img" "$rd/virt/disk.img"
# roconf mode: the image layout for gs.conf as three virtio disks (never inside the initramfs, so the guest mounts real filesystems):
#   vda = rootfs (ext4, mounted -o ro by the guest; /etc/gs.conf is a symlink to /config/gs.conf, fstab/samba/kernel cmdline like the image),
#   vdb = /config (ext4, writable, empty), vdc = /config on FAT (empty; only when mkfs.fat exists or can be fetched)
drives=()
case "$modes" in *roconf*)
	rs="$work/rootfs-src"
	mkdir -p "$rs"/etc/{kernel,samba,default,network/interfaces.d,systemd/network,NetworkManager/system-connections} "$rs/boot/dtbo"
	ln -s /config/gs.conf "$rs/etc/gs.conf"
	echo "root=LABEL=rootfs console=ttyS2,1500000n8" >"$rs/etc/kernel/cmdline"; cp "$rs/etc/kernel/cmdline" "$rs/etc/kernel/cmdline.bak"
	printf '/dev/vdd /Videos exfat defaults,nofail 0 0\n' >"$rs/etc/fstab"
	printf '[global]\n[Videos]\n   path = /Videos\n' >"$rs/etc/samba/smb.conf"
	echo gs-virt >"$rs/etc/hostname"
	mke2fs -q -F -t ext4 -d "$rs" "$work/root.img" 24M >/dev/null 2>&1 || skip "mke2fs for the roconf rootfs image failed"
	mke2fs -q -F -t ext4 "$work/cfg-ext4.img" 16M >/dev/null 2>&1 || skip "mke2fs for the roconf config image failed"
	drives=(-drive "file=$work/root.img,if=virtio,format=raw,cache=unsafe" -drive "file=$work/cfg-ext4.img,if=virtio,format=raw,cache=unsafe")
	mkfat="$(command -v mkfs.fat || command -v mkfs.vfat || true)"
	if [ -z "$mkfat" ]; then
		fdir="$(find "$CACHE" -maxdepth 1 -name 'dosfstools-*' | sort -V | tail -1)"
		if [ -z "$fdir" ] || [ ! -x "$fdir/usr/sbin/mkfs.fat" ]; then
			mkdir -p "$work/fdeb"
			if ( cd "$work/fdeb" && apt-get download dosfstools >/dev/null 2>&1 ) && ls "$work"/fdeb/dosfstools_*.deb >/dev/null 2>&1; then
				fv="$(dpkg-deb -f "$work"/fdeb/dosfstools_*.deb Version)"; fdir="$CACHE/dosfstools-$fv"; mkdir -p "$fdir"
				dpkg-deb -x "$work"/fdeb/dosfstools_*.deb "$fdir" || fdir=""
			else fdir=""; fi
		fi
		[ -z "$fdir" ] || mkfat="$fdir/usr/sbin/mkfs.fat"
	fi
	if [ -n "$mkfat" ] && [ -x "$mkfat" ] && "$mkfat" -F 16 -C "$work/cfg-vfat.img" 16384 >/dev/null 2>&1; then
		drives+=(-drive "file=$work/cfg-vfat.img,if=virtio,format=raw,cache=unsafe")
	else echo "NOTE: no mkfs.fat: the FAT /config variant of roconf will be SKIPped" >&2; fi ;;
esac
( cd "$rd" && find . | cpio -o -H newc --quiet 2>/dev/null | gzip -1 >"$work/initrd.gz" ) || skip "cpio failed"

# ------------------------------------------------------------------ run
accel=(); [ -w /dev/kvm ] && accel=(-enable-kvm -cpu host)
log="$work/serial.log"
qemu_timeout=420; case "$modes" in *roconf*) qemu_timeout=$((qemu_timeout + 420)) ;; esac
nice -n 5 timeout "$qemu_timeout" qemu-system-x86_64 "${accel[@]}" -m 1G -smp 2 -kernel "$kdir/boot/vmlinuz-$ver" -initrd "$work/initrd.gz" "${drives[@]}" \
	-append "console=ttyS0 rdinit=/init quiet virt.mode=$modes virt.oops=${VIRT_OOPS_PROBE:-0}" -display none -no-reboot -serial "file:$log" -monitor none >/dev/null 2>&1
echo "virt: kernel $ver (${accel[*]:-TCG}), libgpiod $(basename "$gdir" | sed 's/^gpiod-//'), modes: $modes"
grep -E '^(SIM-BOOT|SIM-MODE|VIRT-)' "$log" | sed -E 's/^VIRT-PASS/PASS /; s/^VIRT-FAIL/FAIL /; s/^VIRT-INFO/INFO /; s/^VIRT-SKIP/SKIP /'
nf="$(grep -c '^VIRT-FAIL' "$log")"; np="$(grep -c '^VIRT-PASS' "$log")"; ns="$(grep -c '^VIRT-SKIP' "$log")"
[ -z "${VIRT_KEEP_LOG:-}" ] || cp "$log" "$VIRT_KEEP_LOG"
if ! grep -q '^SIM-DONE' "$log"; then echo "FAIL  guest did not finish (no SIM-DONE)"; echo "---- guest log tail"; tail -25 "$log"; exit 1; fi
echo "virt($modes): $np passed, $nf failed, $ns skipped"
[ "$nf" = 0 ] && [ "$np" -gt 0 ]
