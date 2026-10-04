. "$HERE/cases/init/_common.inc"
case_setup() {
	init_sb
	sleep_limit=4; : > "$ROOT/config/before.txt"   # loop "while [ -f /config/before.txt ]" ends via the sleep shim (exit 143)
}
