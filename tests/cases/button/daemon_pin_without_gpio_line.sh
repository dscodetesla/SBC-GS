. "$HERE/cases/button/_common.inc"
# the pin names resolve to no GPIO line on this board (gpiofind prints nothing): every button reports it and exits, no busy loop
sort_shim_log=1   # the per-button loops run as background jobs
script_args=()
case_setup() {
	button_sb host
	printf '#!/bin/bash\necho "gpiofind $*" >> "$SHIM_LOG"\nexit 1\n' > "$ROOT/shims/gpiofind"; chmod +x "$ROOT/shims/gpiofind"
}
