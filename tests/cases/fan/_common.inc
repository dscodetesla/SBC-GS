# shared setup for fan.sh cases: fake pwm + thermal sysfs.   usage: fan_sb <temp_millideg> <initial_duty>
script_under_test=gs/fan.sh
invocation=standalone
dump_baseline=0
sleep_limit=3        # 1 pre-loop sleep + 2 loop iterations, then the shim ends the script (exit 143)
dump_files=(sys/class/pwm/pwmchip14/pwm0/period sys/class/pwm/pwmchip14/pwm0/duty_cycle sys/class/pwm/pwmchip14/pwm0/enable sys/class/pwm/pwmchip14/pwm0/polarity run/pixelpilot.msg)
fan_sb() {
	mkdir -p "$ROOT/sys/class/pwm/pwmchip14/pwm0" "$ROOT/sys/class/thermal/thermal_zone0" "$ROOT/run"
	echo "$1" > "$ROOT/sys/class/thermal/thermal_zone0/temp"
	echo "$2" > "$ROOT/sys/class/pwm/pwmchip14/pwm0/duty_cycle"
}
