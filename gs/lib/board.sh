#!/bin/bash
# Board contract loader (NOT sourced by any gs/*.sh yet).
# Usage: . board.sh; board_get KEY
# BOARD env selects the profile (default radxa-zero3); profile = boards/$BOARD/board.conf
# found next to this lib (../boards) or in the installed /gs/boards.

BOARD="${BOARD:-radxa-zero3}"

_board_load() {
	local here dir f=""
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
	if [ -z "${!key:-}" ]; then
		echo "board.sh: key '$key' is not defined for board '$BOARD'" >&2
		return 1
	fi
	printf '%s\n' "${!key}"
}

_board_load || return 1
