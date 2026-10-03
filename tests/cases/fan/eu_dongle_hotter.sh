. "$HERE/cases/fan/_common.inc"
case_setup() {
	fan_sb 40000 16000; mkdir -p "$ROOT/proc/net/rtl88x2eu/wlx1"; for i in 1 2 3 4 5 6 7 8 9 10; do : > "$ROOT/proc/net/rtl88x2eu/f$i"; done
	echo "thermal state: 62" > "$ROOT/proc/net/rtl88x2eu/wlx1/thermal_state"
}
