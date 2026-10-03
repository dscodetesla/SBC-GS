#!/usr/bin/env bash
# Virtual TX12 (EdgeTX USB joystick) against the REAL EvdevSource of bench/tx12_bridge.py (docs/SIM-EVDEV.md).
#   tests/sim/evdev/run.sh --check   offline, <15 s: py_compile + the tests that need no kernel device (map/profile/HID-descriptor
#                                    consistency, EvdevSource against an in-process fake of the evdev module). Device tests are
#                                    reported as SKIP when /dev/uinput (or /dev/uhid) is not usable here. Exit 0 = nothing failed.
#   tests/sim/evdev/run.sh device    the kernel-device tests directly on this machine (needs /dev/uinput, python-evdev, pymavlink);
#                                    exit 77 when the machine cannot do it
#   tests/sim/evdev/run.sh all       the full suite inside a QEMU x86_64 guest (stock Ubuntu generic kernel with built-in uinput,
#                                    uhid/hid-generic modules, REAL python-evdev + pymavlink + the bridge). Rootless, TCG or KVM.
#   tests/sim/evdev/run.sh guest-shell   same VM, but only prints how it was assembled (debug aid)
# Env: PY (python with pymavlink; default /opt/sbcvenv/bin/python, else python3), SIM_CACHE (default ~/.cache/sbc-gs-sim; the cached
#      kernel-*-generic of tests/sim/virt/run.sh is reused, nothing is downloaded when it is there), EVDEV_PIP_SPEC (default
#      'evdev==2.0.0', built once into $SIM_CACHE/evdev-site through the configured pip/proxy; no TLS or proxy bypass),
#      EVDEV_ROOT (repo whose bench/ and config/ are tested, default this repo; mutate.sh uses a COPY),
#      EVDEV_KEEP_LOG=<file> (copy the guest serial log), EVDEV_ONLY=<unittest -k pattern> (guest: run a subset), EVDEV_TIMEOUT (s, default 900).
# Exit: 0 pass, 1 fail, 77 skipped (a prerequisite is missing, nothing was tested). Never touches real hardware or an aircraft.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
ROOT="${EVDEV_ROOT:-$REPO}"
CACHE="${SIM_CACHE:-$HOME/.cache/sbc-gs-sim}"
PY="${PY:-}"
if [ -z "$PY" ]; then [ -x /opt/sbcvenv/bin/python ] && PY=/opt/sbcvenv/bin/python || PY=python3; fi
export PYTHONDONTWRITEBYTECODE=1
export EVDEV_ROOT="$ROOT"
skip() { echo "SKIP evdev: $*"; exit 77; }
usage() { sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 2; }
command -v "$PY" >/dev/null 2>&1 || skip "$PY not found"

mode="${1:---check}"
case "$mode" in
	--check)
		for f in "$HERE"/*.py "$ROOT/bench/tx12_bridge.py"; do
			"$PY" -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f" || { echo "FAIL evdev: syntax $f"; exit 1; }
		done
		bash -n "$HERE/run.sh" "$HERE/guest/init.sh" 2>/dev/null || { echo "FAIL evdev: bash -n"; exit 1; }
		log="$(mktemp)"; trap 'rm -f "$log"' EXIT
		if "$PY" "$HERE/test_evdev.py" -v >"$log" 2>&1; then
			n="$(sed -n 's/^Ran \([0-9]*\) test.*/\1/p' "$log")"
			sk="$(sed -n 's/^OK (skipped=\([0-9]*\))$/\1/p' "$log")"
			echo "PASS evdev: ${n:-?} tests, skipped=${sk:-0}"
			if [ "${sk:-0}" != 0 ]; then
				grep -E "skipped '" "$log" | sed -E "s/^.*skipped '(.*)'$/SKIP evdev-device: \1/" | sort | uniq -c | sed 's/^ *//' | head -4
				echo "SKIP evdev-device: kernel-device tests not run here (rc 77 equivalent); run tests/sim/evdev/run.sh all (QEMU) or device (needs /dev/uinput)"
			fi
		else
			tail -40 "$log"; echo "FAIL evdev"; exit 1
		fi
		exit 0 ;;
	device)
		"$PY" -c 'import evdev, pymavlink' 2>/dev/null || skip "python-evdev and pymavlink are needed for $PY"
		"$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import vdev; sys.exit(0 if vdev.available("uinput")[0] else 1)' "$HERE" \
			|| skip "no usable /dev/uinput on this machine ($(cd "$HERE" && "$PY" -c 'import vdev; print(vdev.available("uinput")[1])'))"
		exec "$PY" "$HERE/test_evdev.py" -v ;;
	all|guest-shell) ;;
	*) usage ;;
esac
[ -z "${2:-}" ] || usage

# ------------------------------------------------------------------ prerequisites for the VM
for t in qemu-system-x86_64 cpio gzip zstd depmod modprobe python3 ldd; do command -v "$t" >/dev/null || skip "$t not installed"; done
BB="$(command -v busybox || true)"; [ -n "$BB" ] || skip "busybox-static not installed"
file "$BB" 2>/dev/null | grep -q 'statically linked' || skip "$BB is not static (apt install busybox-static)"
"$PY" -c 'import pymavlink' 2>/dev/null || skip "pymavlink missing for $PY (set PY=<venv with pymavlink>)"
work="$(mktemp -d)"; trap '[ -z "${EVDEV_KEEP_LOG:-}" ] || cp "$work/serial.log" "$EVDEV_KEEP_LOG" 2>/dev/null; rm -rf "$work"' EXIT

# kernel: the one tests/sim/virt/run.sh caches (Ubuntu generic: CONFIG_INPUT_UINPUT=y, CONFIG_INPUT_EVDEV=y, HID/UHID/HID_GENERIC=m)
ver="${KERNEL_PKG_VER:-}"
[ -n "$ver" ] || ver="$(find "$CACHE" -maxdepth 1 -name 'kernel-*-generic' -printf '%f\n' 2>/dev/null | sed 's/^kernel-//' | sort -V | tail -1)"
[ -n "$ver" ] || skip "no cached kernel in $CACHE (run tests/sim/virt/run.sh once, or set KERNEL_PKG_VER and have apt sources)"
kdir="$CACHE/kernel-$ver"
if [ ! -f "$kdir/boot/vmlinuz-$ver" ] || [ ! -f "$kdir/lib/modules/$ver/kernel/drivers/hid/uhid.ko.zst" ]; then
	command -v apt-get >/dev/null && command -v dpkg-deb >/dev/null || skip "kernel $ver not cached and apt-get/dpkg-deb missing"
	rm -rf "$kdir"; mkdir -p "$kdir" "$work/deb"
	( cd "$work/deb" && apt-get download "linux-image-unsigned-$ver" "linux-modules-$ver" "linux-modules-extra-$ver" >/dev/null 2>&1 ) \
		|| skip "apt-get download of the kernel packages for $ver failed"
	for d in "$work"/deb/*.deb; do dpkg-deb -x "$d" "$kdir" || skip "dpkg-deb -x failed"; done
fi
M="$kdir/lib/modules/$ver"
[ -f "$M/modules.dep" ] || depmod -b "$kdir" "$ver" 2>/dev/null || skip "depmod failed"

# python-evdev: built once from PyPI into the cache (needs gcc + kernel headers + the configured pip/proxy)
site="$CACHE/evdev-site"
spec="${EVDEV_PIP_SPEC:-evdev==2.0.0}"
if ! PYTHONPATH="$site" "$PY" -c 'import evdev' 2>/dev/null; then
	command -v gcc >/dev/null || skip "gcc missing: python-evdev has to be built (pip install $spec)"
	rm -rf "$site"; mkdir -p "$site"
	"$PY" -m pip install --quiet --target "$site" "$spec" >"$work/pip.log" 2>&1 || { rm -rf "$site"; skip "pip install $spec failed: $(tail -2 "$work/pip.log" | tr '\n' ' ')"; }
fi

# ------------------------------------------------------------------ initramfs
rd="$work/rd"
mkdir -p "$rd"/usr/{bin,sbin,lib,lib64} "$rd"/{proc,sys,dev,run,tmp,etc,mods,repo,py}
ln -s usr/bin "$rd/bin"; ln -s usr/sbin "$rd/sbin"; ln -s usr/lib "$rd/lib"; ln -s usr/lib64 "$rd/lib64"
cp "$BB" "$rd/usr/bin/busybox"
for a in sh ls cat mount insmod poweroff sleep echo uname grep kill sed tr timeout dmesg ifconfig ip mkdir chmod chown cp rm; do ln -sf busybox "$rd/usr/bin/$a"; done
copy_libs() { # shared libraries of a binary, same absolute paths (merged-usr layout)
	ldd "$1" 2>/dev/null | awk '/=>/{print $3} /ld-linux/{print $1}' | while read -r l; do
		[ -f "$l" ] || continue
		mkdir -p "$rd$(dirname "$l")"; [ -e "$rd$l" ] || cp -L "$l" "$rd$l"
	done
}
# the interpreter and its standard library, at the same absolute paths (python locates its prefix from the executable)
read -r pyexe pystd pylib < <("$PY" -c 'import sys, sysconfig; print(sys._base_executable, sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib"))')
pyexe="$(readlink -f "$pyexe")"
mkdir -p "$rd$(dirname "$pyexe")" "$rd$pystd"
cp -L "$pyexe" "$rd$pyexe"; copy_libs "$pyexe"
tar -C "$pystd" --exclude='./test' --exclude='./idlelib' --exclude='./tkinter' --exclude='./turtledemo' --exclude='./ensurepip' \
	--exclude='./lib2to3' --exclude='./site-packages' --exclude='./dist-packages' --exclude='./config-*' -cf - . | tar -C "$rd$pystd" -xf -
[ "$pylib" = "$pystd" ] || { mkdir -p "$rd$pylib"; tar -C "$pylib" -cf - . | tar -C "$rd$pylib" -xf -; }
for so in "$rd$pystd"/lib-dynload/*.so; do [ -f "$so" ] && copy_libs "$so"; done
# the interpreter name the guest uses
mkdir -p "$rd/usr/local/bin"; ln -sf "$pyexe" "$rd/usr/local/bin/python"
# third-party: trimmed pymavlink (default dialect 'all' + ardupilotmega + common), fastcrc, python-evdev, all pre-compiled
pmdir="$("$PY" -c 'import pymavlink, os; print(os.path.dirname(os.path.dirname(pymavlink.__file__)))')"
mkdir -p "$rd/py/site"
( cd "$pmdir" && tar --exclude='pymavlink/dialects/v*/*' --exclude='pymavlink/tools' --exclude='pymavlink/dfindexer' --exclude='__pycache__' -cf - pymavlink fastcrc 2>/dev/null ) | tar -C "$rd/py/site" -xf -
for v in v10 v20; do
	mkdir -p "$rd/py/site/pymavlink/dialects/$v"
	for f in __init__ all ardupilotmega common; do cp -a "$pmdir/pymavlink/dialects/$v/$f.py" "$rd/py/site/pymavlink/dialects/$v/"; done
done
cp -a "$site/evdev" "$rd/py/site/"
"$PY" -m compileall -q -j 2 --invalidation-mode unchecked-hash "$rd/py/site" >/dev/null 2>&1 || true
# the repo parts under test: the bridge, the layered config loader (+ registry), the test directory
mkdir -p "$rd/repo/bench" "$rd/repo/config" "$rd/repo/tests/sim/evdev"
cp -a "$ROOT/bench/tx12_bridge.py" "$ROOT/bench/tx12_map.example.json" "$rd/repo/bench/"
cp -a "$ROOT/config/." "$rd/repo/config/"
cp -a "$HERE/." "$rd/repo/tests/sim/evdev/"
rm -rf "$rd/repo/tests/sim/evdev/__pycache__"
echo "nobody:x:65534:65534:nobody:/:/bin/false" >"$rd/etc/passwd"; echo "nogroup:x:65534:" >"$rd/etc/group"
# kernel modules for the HID path (uinput and evdev are built in), decompressed, dependency order precomputed
: >"$rd/mods/order"
for m in hid hid-generic uhid; do
	modprobe -d "$kdir" -S "$ver" --show-depends "$m" 2>/dev/null | awk '$1=="insmod"{print $2}' | while read -r p; do
		bn="$(basename "$p" .zst)"
		[ -f "$rd/mods/$bn" ] || zstd -dqc "$p" >"$rd/mods/$bn" || exit 1
		grep -qx "$bn" "$rd/mods/order" || echo "$bn" >>"$rd/mods/order"
	done
done
cp "$HERE/guest/init.sh" "$rd/init"; chmod +x "$rd/init"
( cd "$rd" && find . | cpio -o -H newc --quiet 2>/dev/null | gzip -1 >"$work/initrd.gz" ) || skip "cpio failed"
if [ "$mode" = guest-shell ]; then echo "initramfs $(du -sh "$work/initrd.gz" | cut -f1), kernel $ver, python $pyexe, evdev from $site"; exit 0; fi

# ------------------------------------------------------------------ run
accel=(); [ -w /dev/kvm ] && accel=(-enable-kvm -cpu host)
log="$work/serial.log"
t0="$(date +%s)"
nice -n 5 timeout "${EVDEV_TIMEOUT:-900}" qemu-system-x86_64 "${accel[@]}" -m 1G -smp 2 -kernel "$kdir/boot/vmlinuz-$ver" -initrd "$work/initrd.gz" \
	-append "console=ttyS0 rdinit=/init quiet evdev.only=${EVDEV_ONLY:-}" -display none -no-reboot -serial "file:$log" -monitor none >/dev/null 2>&1
evv="$(PYTHONPATH="$site" "$PY" -c 'from importlib.metadata import version; print(version("evdev"))' 2>/dev/null)"
echo "evdev-vm: kernel $ver (${accel[*]:-TCG}), python-evdev ${evv:-?}, $(( $(date +%s) - t0 )) s"
grep -E '^(EVDEV-|test_|[A-Za-z_]+ \(|Ran |OK|FAILED|FAIL:|ERROR:|Traceback|  File|[A-Za-z]*Error|AssertionError|INFO )' "$log" | sed -E 's/\r$//' | head -${EVDEV_LINES:-400}
if ! grep -q '^EVDEV-DONE' "$log"; then echo "FAIL  guest did not finish (no EVDEV-DONE)"; echo "---- guest log tail"; tail -25 "$log"; exit 1; fi
rc="$(sed -n 's/^EVDEV-RC \([0-9]*\).*/\1/p' "$log" | tail -1)"
[ "${rc:-1}" = 0 ] && { echo "PASS evdev-vm: $(sed -n 's/^Ran \([0-9]*\) test.*/\1/p' "$log" | tail -1) tests, $(sed -n 's/^OK (skipped=\([0-9]*\))$/skipped=\1/p' "$log" | tail -1)"; exit 0; }
echo "FAIL evdev-vm (guest test rc=${rc:-?})"; exit 1
