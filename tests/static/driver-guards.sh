#!/usr/bin/env bash
# Static check of the Pi 5 / Ubuntu-host fixes: bench/apply-patches.sh on a mock driver tree (match, skip, idempotent, failure),
# hw_fan_kernel_managed / otg_supported per board profile, and the blacklist lines written by bench/install-driver.sh.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
bad=0
want() { if [ "$2" = "$3" ]; then echo "ok $1 = $3"; else echo "FAIL $1: expected '$2' got '$3'"; bad=1; fi; }
G() { GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null git -c user.name=t -c user.email=t@t -c commit.gpgsign=false "$@"; }

echo "== apply-patches on a mock driver tree"
src="$T/src"; mkdir -p "$src/p"; cd "$src" || exit 1
G init -q . ; printf 'int f(int a)\n{\n\treturn a;\n}\n' > drv.c; G add drv.c; G commit -q -m base
cp drv.c "$T/new.c"; sed -i 's/int a/int a, int b/' "$T/new.c"
G diff --no-index --no-color drv.c "$T/new.c" | sed "s#$T/new.c#b/drv.c#; s#a/drv.c#a/drv.c#; s#^--- drv.c#--- a/drv.c#" > "$T/0001-mock.patch"
sed -i 's#^--- a/drv.c#--- a/drv.c#; s#^+++ .*#+++ b/drv.c#' "$T/0001-mock.patch"
printf '0001-mock.patch\trtlmock\tk612*\n# comment\n\n0001-mock.patch\totherdrv\tk612*\n' > "$T/series"
A="$REPO/bench/apply-patches.sh"
"$A" "$src" rtlmock 6.8.0-146-generic "$T/series" >/dev/null 2>&1; want "kernel 6.8 does not match k612*: rc" 0 $?
want "kernel 6.8: file untouched" "int f(int a)" "$(sed -n 1p "$src/drv.c")"
printf '0001-mock.patch\totherdrv\tk612*\n' > "$T/series-other"; cp "$T/0001-mock.patch" "$T/0001-mock.patch.bak"
"$A" "$src" rtlmock 6.12.109+rpt-rpi-2712 "$T/series-other" >/dev/null 2>&1; want "series row of another driver: rc" 0 $?
want "series row of another driver: file untouched" "int f(int a)" "$(sed -n 1p "$src/drv.c")"
"$A" "$src" rtlmock 6.12.109+rpt-rpi-2712 "$T/series" >/dev/null 2>&1; want "kernel 6.12 applies: rc" 0 $?
want "kernel 6.12: file patched" "int f(int a, int b)" "$(sed -n 1p "$src/drv.c")"
"$A" "$src" rtlmock 6.12.109+rpt-rpi-v8 "$T/series" 2>&1 | grep -q 'already applied'; want "second run is idempotent: already applied" 0 $?
echo 'x' >> "$src/drv.c"; G -C "$src" checkout -q -- drv.c; sed -i '1s/.*/int g(void)/' "$src/drv.c"
"$A" "$src" rtlmock 6.12.109+rpt-rpi-2712 "$T/series" >/dev/null 2>&1; want "patch that does not apply: rc" 1 $?
"$A" "$T" rtlmock 6.12.1 "$T/series" >/dev/null 2>&1; want "not a git tree: rc" 2 $?

echo "== board predicates"
cd "$REPO" || exit 1
for b in radxa-zero3 rpi4 rpi5; do
	f="$(BOARD=$b bash -c ". '$REPO/gs/lib/hw.sh'; hw_fan_kernel_managed && echo yes || echo no")"
	o="$(BOARD=$b bash -c ". '$REPO/gs/lib/otg.sh'; otg_supported && echo yes || echo no")"
	echo "$b fan_kernel_managed=$f otg_supported=$o"
done
want "rpi5 fan is kernel managed" yes "$(BOARD=rpi5 bash -c ". '$REPO/gs/lib/hw.sh'; hw_fan_kernel_managed && echo yes || echo no")"
want "radxa fan is not kernel managed" no "$(BOARD=radxa-zero3 bash -c ". '$REPO/gs/lib/hw.sh'; hw_fan_kernel_managed && echo yes || echo no")"
want "unknown board falls back to Radxa behaviour (fan runs)" no "$(BOARD=nope bash -c ". '$REPO/gs/lib/hw.sh'; hw_fan_kernel_managed && echo yes || echo no")"
want "rpi5 has no OTG role switch" no "$(BOARD=rpi5 bash -c ". '$REPO/gs/lib/otg.sh'; otg_supported && echo yes || echo no")"

echo "== install-driver.sh wiring"
grep -q 'blacklist rtw88_8812au' "$REPO/bench/install-driver.sh"; want "blacklist rtw88_8812au" 0 $?
grep -q 'blacklist rtw88_8821au' "$REPO/bench/install-driver.sh"; want "blacklist rtw88_8821au" 0 $?
grep -q 'apply-patches.sh' "$REPO/bench/install-driver.sh"; want "patch series applied before dkms-install" 0 $?
awk '/apply-patches.sh/{p=NR} /dkms-install.sh/{d=NR} END{exit !(p && d && p<d)}' "$REPO/bench/install-driver.sh"; want "patches are applied BEFORE dkms-install" 0 $?
echo "bad=$bad"
exit "$bad"
