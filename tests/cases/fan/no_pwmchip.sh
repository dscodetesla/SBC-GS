. "$HERE/cases/fan/_common.inc"
case_setup() {
	mkdir -p "$ROOT/sys/class/thermal/thermal_zone0" "$ROOT/run"; echo 45000 > "$ROOT/sys/class/thermal/thermal_zone0/temp"
}
