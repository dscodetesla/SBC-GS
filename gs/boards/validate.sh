#!/bin/bash
# Validate board profiles against REQUIRED_KEYS.
# Usage: validate.sh [board-dir ...]   (default: every dir under boards/)
# Exit 0 if all pass, 1 if any profile is invalid.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mapfile -t required < <(grep -Ev '^[[:space:]]*(#|$)' "$HERE/REQUIRED_KEYS")
dirs=("$@")
if [ ${#dirs[@]} -eq 0 ]; then
	for d in "$HERE"/*/; do dirs+=("${d%/}"); done
fi
bad=0
for d in "${dirs[@]}"; do
	conf="$d/board.conf"
	name="$(basename "$d")"
	if [ ! -f "$conf" ]; then echo "FAIL $name: no board.conf"; bad=1; continue; fi
	# Only plain KEY='value' / KEY="value" / KEY=value lines (and comments/blank) are allowed.
	nl=0; syn=0
	while IFS= read -r line; do
		nl=$((nl+1))
		[[ "$line" =~ ^[[:space:]]*(#.*)?$ ]] && continue
		[[ "$line" =~ ^[A-Z][A-Z0-9_]*=(\'[^\']*\'|\"[^\"\$\`]*\"|[A-Za-z0-9_./:@%+-]*)[[:space:]]*(#.*)?$ ]] && continue
		echo "FAIL $name: $conf:$nl: bad syntax: $line"; syn=1
	done < "$conf"
	if [ "$syn" = 1 ]; then bad=1; continue; fi
	pbad=0
	for k in "${required[@]}"; do
		if ! grep -Eq "^[[:space:]]*${k}=" "$conf"; then echo "FAIL $name: missing key $k"; pbad=1; continue; fi
		# shellcheck disable=SC1090
		v="$(unset "$k"; . "$conf" >/dev/null 2>&1; printf '%s' "${!k:-}")"
		if [ -z "$v" ]; then echo "FAIL $name: empty key $k"; pbad=1; fi
	done
	# Optional keys with a fixed set of values: absent is fine (library default = Radxa behaviour), a bad value is not.
	for spec in GPIO_PIN_NUMBERING:physical,bcm DTBO_MODE:rename,config-txt PART_TABLE:gpt,mbr; do
		k="${spec%%:*}"
		grep -Eq "^[[:space:]]*${k}=" "$conf" || continue
		# shellcheck disable=SC1090
		v="$(unset "$k"; . "$conf" >/dev/null 2>&1; printf '%s' "${!k:-}")"
		case ",${spec#*:}," in *",$v,"*) ;; *) echo "FAIL $name: bad value $k='$v' (allowed: ${spec#*:})"; pbad=1 ;; esac
	done
	# GPIO_PIN_MAP is a file name relative to the board dir: no absolute path, no "..", and the file must exist.
	if grep -Eq '^[[:space:]]*GPIO_PIN_MAP=' "$conf"; then
		# shellcheck disable=SC1090
		v="$(unset GPIO_PIN_MAP; . "$conf" >/dev/null 2>&1; printf '%s' "${GPIO_PIN_MAP:-}")"
		case "$v" in
			''|/*|*..*) echo "FAIL $name: bad GPIO_PIN_MAP '$v' (relative file name without '..' required)"; pbad=1 ;;
			*) [ -f "$d/$v" ] || { echo "FAIL $name: GPIO_PIN_MAP file '$v' not found"; pbad=1; } ;;
		esac
	fi
	if [ "$pbad" = 0 ]; then echo "ok $name"; else bad=1; fi
done
exit $bad
