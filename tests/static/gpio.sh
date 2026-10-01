#!/usr/bin/env bash
# Static check of gs/lib/gpio.sh: gpio_find must call `gpiofind PIN_<n>` (Radxa prefix) and pass its output through.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"
printf '#!/bin/bash\necho "gpiofind $*" >> "$SHIM_LOG"\necho "gpiochip0 ${1#PIN_}"\n' > "$T/bin/gpiofind"; chmod +x "$T/bin/gpiofind"
export SHIM_LOG="$T/log"; : > "$SHIM_LOG"
bad=0
echo "== gpio_find"
for n in 22 32; do
	out="$(PATH="$T/bin:/usr/bin:/bin" bash -c ". '$REPO/gs/lib/gpio.sh' && gpio_find $n")"
	echo "pin $n -> $out"
done
echo "== shim log"; cat "$SHIM_LOG"
[ "$(cat "$SHIM_LOG")" = "$(printf 'gpiofind PIN_22\ngpiofind PIN_32')" ] && echo "ok argv" || { echo "FAIL argv"; bad=1; }
echo "== failure status"
printf '#!/bin/bash\nexit 3\n' > "$T/bin/gpiofind"
PATH="$T/bin:/usr/bin:/bin" bash -c ". '$REPO/gs/lib/gpio.sh' && gpio_find 22"; echo "rc=$?"
echo "bad=$bad"
exit $bad
