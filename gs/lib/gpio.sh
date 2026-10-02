#!/bin/bash
# GPIO helpers (milestone M3a). Thin wrappers that run the same libgpiod commands as before.
# Usage: . /gs/lib/gpio.sh; gpio_find <pin-number>
# Only gpiofind is wrapped; gpioset/gpioget/gpiomon call sites stay as they were.

_GPIO_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Print the GPIO_PIN_PREFIX of the active board (fallback 'PIN_' when the board lib or key is unavailable).
# Done in a subshell so board.conf variables never leak into the calling script.
_gpio_pin_prefix() {
	local prefix
	prefix="$( . "$_GPIO_LIB_DIR/board.sh" 2>/dev/null && board_get GPIO_PIN_PREFIX 2>/dev/null )" || prefix='PIN_'
	printf '%s' "$prefix"
}

# _gpio_get KEY FALLBACK: print board_get KEY, or FALLBACK when the board lib/profile/key is unavailable (subshell: no leaks).
_gpio_get() {
	local v
	v="$( . "$_GPIO_LIB_DIR/board.sh" 2>/dev/null && board_get "$1" 2>/dev/null )" || v="$2"
	printf '%s' "$v"
}

# Directory of the active board profile (same search order as board.sh); empty when not found.
_gpio_board_dir() {
	local dir
	for dir in /gs/boards "$_GPIO_LIB_DIR/../boards"; do
		if [ -f "$dir/${BOARD:-radxa-zero3}/board.conf" ]; then (cd "$dir/${BOARD:-radxa-zero3}" && pwd); return 0; fi
	done
	return 1
}

# _gpio_map_lookup <map-file> <physical-pin>: print the BCM number for a 40-pin header pin from a "<pin> <bcm|label>" data file.
# Non-GPIO pins (3V3/5V/GND) and unknown pins fail with a message on stderr.
_gpio_map_lookup() {
	local map="$1" pin="$2" val
	val="$(awk -v p="$pin" '$1 !~ /^#/ && NF >= 2 && $1 == p { print $2; exit }' "$map")"
	if [ -z "$val" ]; then echo "gpio.sh: physical pin '$pin' is not in $map" >&2; return 1; fi
	case "$val" in
		*[!0-9]*) echo "gpio.sh: physical pin $pin is $val, not a GPIO line" >&2; return 1 ;;
	esac
	printf '%s' "$val"
}

# gpio_find <pin-number>: prints/returns what `gpiofind <name>` does. Optional board keys select the name:
#   GPIO_PIN_NUMBERING=physical (default): name = ${GPIO_PIN_PREFIX}<n> (Radxa: PIN_<n>); with GPIO_PIN_MAP (file relative to the
#     board dir, "<physical pin> <bcm|label>" per line) <n> is first translated physical pin -> BCM number (Pi: GPIO<bcm>).
#   GPIO_PIN_NUMBERING=bcm: <n> is already a BCM number, name = ${GPIO_PIN_PREFIX}<n>.
# Without these keys the behaviour is exactly the legacy `gpiofind ${GPIO_PIN_PREFIX}<n>`.
gpio_find() {
	local mode map dir n="${1-}" prefix
	mode="$(_gpio_get GPIO_PIN_NUMBERING physical)"
	map="$(_gpio_get GPIO_PIN_MAP '')"
	prefix="$(_gpio_pin_prefix)"
	case "$mode" in
		physical)
			if [ -n "$map" ]; then
				dir="$(_gpio_board_dir)" || { echo "gpio.sh: board dir not found for GPIO_PIN_MAP '$map'" >&2; return 1; }
				[ -f "$dir/$map" ] || { echo "gpio.sh: pin map '$dir/$map' not found" >&2; return 1; }
				n="$(_gpio_map_lookup "$dir/$map" "$n")" || return 1
			fi ;;
		bcm)
			case "$n" in ''|*[!0-9]*) echo "gpio.sh: BCM pin '$n' is not a number" >&2; return 1 ;; esac ;;
		*) echo "gpio.sh: unknown GPIO_PIN_NUMBERING '$mode' (physical|bcm)" >&2; return 1 ;;
	esac
	gpiofind "${prefix}${n}"
}
