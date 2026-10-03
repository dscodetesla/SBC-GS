#!/bin/bash
# Board contract loader (NOT sourced by any gs/*.sh yet).
# Usage: . board.sh; board_get KEY
# The profile is chosen by, in this order: the BOARD env, the one-line file /etc/gs-board (GS_BOARD_FILE; written by gs/install.sh),
# the device-tree model (/proc/device-tree/model, GS_DT_MODEL_FILE) matched against DT_MODEL_PREFIX of the installed profiles, and
# finally radxa-zero3 (the legacy default). A profile = boards/$BOARD/board.conf found next to this lib (../boards) or in /gs/boards.

# _board_detect: print the board id for this machine (never fails; falls back to radxa-zero3).
_board_detect() {
	local here dir f id model best="" bestlen=0 prefix conf
	here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	f="${GS_BOARD_FILE:-/etc/gs-board}"
	if [ -r "$f" ]; then
		id="$(head -n 1 "$f" 2>/dev/null | tr -d '[:space:]')"
		if [[ "$id" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then printf '%s' "$id"; return 0; fi
	fi
	f="${GS_DT_MODEL_FILE:-/proc/device-tree/model}"
	model=""
	if [ -r "$f" ]; then model="$(tr -d '\0' < "$f" 2>/dev/null)"; fi
	if [ -n "$model" ]; then
		for dir in /gs/boards "$here/../boards"; do
			[ -d "$dir" ] || continue
			for conf in "$dir"/*/board.conf; do
				[ -f "$conf" ] || continue
				# data, not code: the value of one line, restricted to a harmless character set
				prefix="$(sed -n "s/^DT_MODEL_PREFIX='\([A-Za-z0-9 ._+-]*\)'\$/\1/p" "$conf" | head -n 1)"
				[ -n "$prefix" ] || continue
				case "$model" in
					"$prefix"*) if [ "${#prefix}" -gt "$bestlen" ]; then best="$(basename "$(dirname "$conf")")"; bestlen="${#prefix}"; fi ;;
				esac
			done
			[ -z "$best" ] || break
		done
	fi
	printf '%s' "${best:-radxa-zero3}"
}

BOARD="${BOARD:-$(_board_detect)}"

_board_load() {
	local here dir f=""
	# BOARD is an identifier, never a path: "../" would source a foreign board.conf as the calling user (D9)
	if ! [[ "$BOARD" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
		echo "board.sh: invalid board id '$BOARD' (expected [a-z0-9][a-z0-9-]*)" >&2
		return 1
	fi
	here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	for dir in /gs/boards "$here/../boards"; do
		if [ -f "$dir/$BOARD/board.conf" ]; then f="$dir/$BOARD/board.conf"; break; fi
	done
	if [ -z "$f" ]; then
		echo "board.sh: unknown board '$BOARD' (no boards/$BOARD/board.conf)" >&2
		return 1
	fi
	# shellcheck disable=SC1090
	. "$f"
}

# Print the value of KEY; fail loudly if unset or empty.
board_get() {
	local key="${1:?board_get: key required}"
	# the key is expanded indirectly (${!key}); anything but a plain variable name would be evaluated as an arithmetic subscript (D10)
	if ! [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
		echo "board.sh: invalid key '$key' (expected [A-Z][A-Z0-9_]*)" >&2
		return 1
	fi
	if [ -z "${!key:-}" ]; then
		echo "board.sh: key '$key' is not defined for board '$BOARD'" >&2
		return 1
	fi
	printf '%s\n' "${!key}"
}

_board_load || return 1
