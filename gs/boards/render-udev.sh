#!/bin/bash
# Render gs/98-rename.rules.in and gs/99-GS.rules.in from a board profile (milestone M3b).
# Usage: render-udev.sh <board-id | board-dir> <out-dir>
# Placeholders @KEY@ are replaced by the value of KEY in board.conf; an unresolved @KEY@ is an error.
# Not used by install.sh yet; for radxa-zero3 the output is byte-identical to the checked-in *.rules.
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GS_DIR="$(cd "$HERE/.." && pwd)"
board="${1:?usage: render-udev.sh <board-id|board-dir> <out-dir>}"
out="${2:?usage: render-udev.sh <board-id|board-dir> <out-dir>}"
if [ -d "$board" ]; then dir="$board"; else dir="$HERE/$board"; fi
[ -f "$dir/board.conf" ] || { echo "render-udev.sh: no board.conf in $dir" >&2; exit 1; }
KEYS="WIFI_ONBOARD_IFACE GADGET_IFNAME WIFI_ONBOARD_DRIVER"
# shellcheck disable=SC1091
. "$dir/board.conf"
mkdir -p "$out"
for name in 98-rename.rules 99-GS.rules; do
	[ -f "$GS_DIR/$name.in" ] || { echo "render-udev.sh: missing template $name.in" >&2; exit 1; }
	# trailing "x" keeps the final newline through command substitution
	content="$(cat "$GS_DIR/$name.in"; printf x)"
	for k in $KEYS; do
		v="${!k:-}"
		[ -n "$v" ] || { if [[ "$content" == *"@$k@"* ]]; then echo "render-udev.sh: key $k is empty in $dir/board.conf" >&2; exit 1; fi; continue; }
		content="${content//"@$k@"/"$v"}"
	done
	if [[ "$content" =~ @[A-Z][A-Z0-9_]*@ ]]; then echo "render-udev.sh: unresolved placeholder ${BASH_REMATCH[0]} in $name.in" >&2; exit 1; fi
	printf '%s' "${content%x}" > "$out/$name"
done
