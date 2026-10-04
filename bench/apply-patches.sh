#!/usr/bin/env bash
# Apply the DKMS patch series (tests/sim/dkms/patches/series) that matches the kernel to a driver source tree.
#   bench/apply-patches.sh <source-dir> <driver-id> <kernel-release> <series-file>
# series line: <patch file> TAB <driver id, e.g. rtl8812au> TAB <kernel-id glob, e.g. k612*>; '#' and empty lines ignored.
# kernel-id = k<major><minor>x of the release (6.12.109+rpt-rpi-2712 -> k612x). Idempotent (an applied patch is skipped).
# Exit: 0 ok (also when nothing matches), 1 a matching patch does not apply, 2 usage.
set -u
[ "$#" = 4 ] || { echo "usage: $0 <source-dir> <driver-id> <kernel-release> <series-file>" >&2; exit 2; }
dir="$1"; drv="$2"; krel="$3"; series="$4"
[ -d "$dir/.git" ] || { echo "apply-patches: $dir is not a git tree" >&2; exit 2; }
[ -f "$series" ] || { echo "apply-patches: no series file $series" >&2; exit 2; }
kid="k$(printf '%s' "$krel" | sed -E 's/^([0-9]+)\.([0-9]+).*/\1\2/')x"
n=0
while IFS=$'\t' read -r pname pdrv pglob; do
	case "$pname" in '' | '#'*) continue ;; esac
	[ "$pdrv" = "$drv" ] || continue
	# shellcheck disable=SC2254  # the series column is a glob pattern by design
	case "$kid" in $pglob) ;; *) continue ;; esac
	patch="$(dirname "$series")/$pname"
	if git -C "$dir" apply --reverse --check "$patch" 2>/dev/null; then
		echo "apply-patches: $pname already applied"
	elif git -C "$dir" apply --check "$patch" 2>/dev/null; then
		git -C "$dir" apply "$patch" || exit 1
		echo "apply-patches: applied $pname (kernel $krel, $kid)"
		n=$((n + 1))
	else
		echo "apply-patches: $pname does not apply to $dir (driver ref changed?), see docs/SIM-DKMS.md" >&2
		exit 1
	fi
done < "$series"
exit 0
