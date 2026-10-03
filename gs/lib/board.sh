#!/bin/bash
# Board contract loader (NOT sourced by any gs/*.sh yet).
# Usage: . board.sh; board_get KEY
# BOARD env selects the profile (default radxa-zero3); profile = boards/$BOARD/board.conf
# found next to this lib (../boards) or in the installed /gs/boards.

BOARD="${BOARD:-radxa-zero3}"

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
