#!/bin/bash
# OTG helpers (milestone M3b). Values come from the board profile; fallbacks are the Radxa Zero 3W literals
# that were hard-coded in otg-gadget.sh / button.sh, so behaviour is unchanged if board.sh or a key is missing.
# Usage: . /gs/lib/otg.sh; otg_mode_file
# Every helper always succeeds (prints a value), so callers' `set -e` semantics are not affected.

_OTG_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# _otg_get KEY FALLBACK: print board_get KEY, or FALLBACK when the board lib/profile/key is unavailable.
# Runs in a subshell so board.conf variables never leak into the calling script.
_otg_get() {
	local v
	v="$( . "$_OTG_LIB_DIR/board.sh" 2>/dev/null && board_get "$1" 2>/dev/null )" || v="$2"
	printf '%s' "$v"
}

# USB OTG controller name (Radxa: fcc00000.dwc3)
otg_controller() { _otg_get OTG_CONTROLLER 'fcc00000.dwc3'; }
# debugfs role switch file (Radxa: /sys/kernel/debug/usb/fcc00000.dwc3/mode)
otg_mode_file() { _otg_get OTG_MODE_FILE '/sys/kernel/debug/usb/fcc00000.dwc3/mode'; }
# mass-storage backing device: default and the preferred alternative when it exists as a block device
otg_mass_default() { _otg_get OTG_MASS_STORAGE_DEFAULT '/dev/mmcblk0p4'; }
otg_mass_alt() { _otg_get OTG_MASS_STORAGE_ALT '/dev/mmcblk1p4'; }
