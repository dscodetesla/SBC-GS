. "$HERE/cases/gs/_common.inc"
# the record partition is found by its label (a Raspberry Pi image has no "<disk>p4"): mount uses /dev/disk/by-label/videos
rewrite_extra=(/dev/disk)
case_setup() {
	gs_sb
	mkdir -p "$ROOT/dev/disk/by-label"; : > "$ROOT/dev/disk/by-label/videos"
}
