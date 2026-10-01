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

# gpio_find <pin-number>: same as `gpiofind ${GPIO_PIN_PREFIX}<n>` (Radxa: `gpiofind PIN_<n>`); stdout and exit status are gpiofind's.
gpio_find() {
	gpiofind "$(_gpio_pin_prefix)${1-}"
}
