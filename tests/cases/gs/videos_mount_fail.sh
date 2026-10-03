. "$HERE/cases/gs/_common.inc"
# fsck.exfat and mount both fail: gs.sh warns and goes on (the boot and the video must not depend on the record partition)
case_setup() {
	gs_sb
	for n in mount fsck.exfat; do printf '#!/bin/bash\necho "%s $*" >> "$SHIM_LOG"\n[ "$1" = --bind ] && exit 0\nexit 1\n' "$n" > "$ROOT/shims/$n"; chmod +x "$ROOT/shims/$n"; done
}
