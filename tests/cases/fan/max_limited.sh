. "$HERE/cases/fan/_common.inc"
case_setup() {
	fan_sb 60000 0; conf_set fan_pwm_max_duty_cycle 21   # limit 8400: one step up (8000->10000), then limited
}
