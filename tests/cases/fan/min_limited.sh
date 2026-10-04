. "$HERE/cases/fan/_common.inc"
case_setup() {
	fan_sb 40000 0; conf_set fan_pwm_min_duty_cycle 20   # min 8000 = start duty: limited at once
}
