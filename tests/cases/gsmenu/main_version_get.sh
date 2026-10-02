. "$HERE/cases/gsmenu/_common.inc"
script_args=(get gs main Version)
case_setup() {
	gm_sb
	echo "VERSION=1.2.3" > "$ROOT/etc/gs-release"
}
